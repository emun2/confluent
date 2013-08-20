# Copyright 2013 IBM
# All rights reserved


# Ultimately, the design is to handle all the complicated stuff at set
# rather than get tiime.  When something is set on a group, then all
# members of that group are examined  and 'inheritedfrom' attributes
# are pushed.  as expression definned values are iinserted, their
#  dependdentt attributes are added to a private dict to aid in auto
# calculation.  When a name is changed, all attributes are re-evaluated
# on get, should be simple read value *except* for encrypted values,
# which are only decrypted when explicitly requested
# encrypted fields do not support expressions, either as a source or
# destination

# For multi-node operation, each instance opens and retains a TLS connection
# to each other instance.  'set' operations push to queue for writeback and
# returns.  The writeback thread writes to local disk and to other instances.
# A function is provided to wait for pending output to disk and peers to complete
# to assure that aa neww requesst to  peer does not beat configuration data to
# the target

# on disk format is cpickle.  No data shall be in the configuration db required
# to get started.  For example, argv shall indicate ports rather than cfg store

# Note on the cryptography.  Default behavior is mostly just to pave the
# way to meaningful security.  Root all potentially sensitive data in
# one key.  That key is in plain sight, so not meaningfully protected
# However, the key can be protected in the following ways:
#   - Passphrase protected (requiring human interaction every restart)
#   - TPM sealing (which would forgo the interactive assuming risk of
#           physical attack on TPM is not a concern)


import array
import ast
import collections
import confluent.crypto
import copy
import cPickle
import eventlet
import fcntl
import math
import operator
import os
import re
import string

_cfgstore = None

def get_global(varname):
    if (_cfgstore is None or 'globals' not in _cfgstore or
            varname not in _cfgstore['globals']):
        return None
    return _cfgstore['globals'][varname]

def set_global(varname, value):
    if _cfgstore is None:
        raise Exception("set_global called before config initted")
    if 'globals' not in _cfgstore:
        _cfgstore['globals'] = { varname: value }
    else:
        _cfgstore['globals'][varname] = value
    ConfigData._bg_sync_to_file()


class _ExpressionFormat(string.Formatter):
    posmatch = re.compile('^n([0-9]*)$')
    nummatch = re.compile('[0-9]+')
    _supported_ops = {
        ast.Mult: operator.mul,
        ast.Div: operator.floordiv,
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.LShift: operator.lshift,
        ast.RShift: operator.rshift,
        ast.BitAnd: operator.and_,
        ast.BitXor: operator.xor,
        ast.BitOr: operator.or_,
    }

    def __init__(self, nodeobj):
        self._nodeobj = nodeobj
        self._numbers = re.findall(self.nummatch, nodeobj['name']['value'])

    def get_field(self, field_name, args, kwargs):
        parsed = ast.parse(field_name)
        return (self._handle_ast_node(parsed.body[0].value), field_name)

    def _handle_ast_node(self, node):
        if isinstance(node, ast.Num):
            if '_expressionkeys' not in self._nodeobj:
                self._nodeobj['_expressionkeys'] = set(['name'])
            else:
                self._nodeobj['_expressionkeys'].add('name')
            return node.n
        elif isinstance(node, ast.Attribute):
            #ok, we have something with a dot
            left = node.value.id
            right = node.attr
            key = left + '.' + right
            if '_expressionkeys' not in self._nodeobj:
                self._nodeobj['_expressionkeys'] = set([key])
            else:
                self._nodeobj['_expressionkeys'] .add([key])
            val = _decode_attribute(key, self._nodeobj,
                                    formatter=self)
            return val['value'] if 'value' in val else ""
        elif isinstance(node, ast.Name):
            var = node.id
            if var == 'nodename':
                return self._nodeobj['name']['value']
            mg = re.match(self.posmatch, var)
            if mg:
                idx = int(mg.group(1))
                return int(self._numbers[idx - 1])
            else:
                if var in self._nodeobj:
                    if '_expressionkeys' not in self._nodeobj:
                        self._nodeobj['_expressionkeys'] = set([key])
                    else:
                        self._nodeobj['_expressionkeys'].add(var)
                    val = _decode_attribute(var, self._nodeobj,
                                            formatter=self)
                    return val['value'] if 'value' in val else ""
        elif isinstance(node, ast.BinOp):
            optype = type(node.op)
            if optype not in self._supported_ops:
                raise Exception("Unsupported operation")
            op = self._supported_ops[optype]
            return op(self._handle_ast_node(node.left),
               self._handle_ast_node(node.right))


def _decode_attribute(attribute, nodeobj, formatter=None, decrypt=False):
    if attribute not in nodeobj:
        return None
    # if we have an expression and a formatter, that overrides 'value'
    # which may be out of date
    # get methods will skip the formatter allowing value to come on through
    # set methods induce recalculation as appropriate to get a cached value
    if 'expression' in nodeobj[attribute] and formatter is not None:
        retdict = copy.deepcopy(nodeobj[attribute])
        retdict['value'] = formatter.format(retdict['expression'])
        return retdict
    elif 'value' in nodeobj[attribute]:
        return nodeobj[attribute]
    elif 'cryptvalue' in nodeobj[attribute] and decrypt:
        retdict = copy.deepcopy(nodeobj[attribute])
        retdict['value'] = confluent.crypto.decrypt_value(
                                nodeobj[attribute]['cryptvalue'])
        return retdict
    return nodeobj[attribute]

# my thinking at this point is that noderange and configdata  objects
# will be constructed and passed as part of a context object to plugins
# reasoning being that the main program will handle establishing the
# tenant context and then modules need not consider the current tenant
# most of the time as things are automatic

class ConfigData(object):
    _cfgfilename = "/etc/confluent/cfgdb"
    _cfgwriter = None
    _writepending = False
    def __init__(self, tenant=0, decrypt=False):
        global _cfgstore
        if _cfgstore is None:
            self._read_from_file()
        self.decrypt = decrypt
        if 'tenant' not in _cfgstore:
            _cfgstore['tenant'] = {}
        if tenant not in _cfgstore['tenant']:
            _cfgstore['tenant'][tenant] = {'id': tenant}
        self._cfgstore = _cfgstore['tenant'][tenant]

    def get_node_attributes(self, nodelist, attributes=[]):
        if 'nodes' not in self._cfgstore:
            return None
        retdict = {}
        if isinstance(nodelist,str):
            nodelist = [nodelist]
        for node in nodelist:
            if node not in self._cfgstore['nodes']:
                continue
            cfgnodeobj = self._cfgstore['nodes'][node]
            nodeobj = {}
            if len(attributes) == 0:
                attributes = cfgnodeobj.keys()
            for attribute in attributes:
                if attribute not in cfgnodeobj:
                    continue
                nodeobj[attribute] = _decode_attribute(attribute, cfgnodeobj,
                                                       decrypt=self.decrypt)
            retdict[node] = nodeobj
        return retdict

    def _sync_groups_to_node(self, groups, node):
        if 'groups' not in self._cfgstore:
            self._cfgstore['groups'] = {}
        for group in self._cfgstore['groups'].keys():
            if group not in groups:
                self._cfgstore['groups'][group]['nodes'].discard(node)
        for group in groups:
            if group not in self._cfgstore['groups']:
                self._cfgstore['groups'][group] = {'name': {'value': group},
                                                      'nodes': set([node]) }
            elif 'nodes' not in self._cfgstore['groups'][group]:
                self._cfgstore['groups'][group]['nodes'] = set([node])
            else:
                self._cfgstore['groups'][group]['nodes'].add(node)
            if 'grouplist' not in self._cfgstore:
                self._cfgstore['grouplist'] = [group]
            elif group not in self._cfgstore['grouplist']:
                self._cfgstore['grouplist'].append(group)

    def _sync_nodes_to_group(self, nodes, group):
        if 'nodes' not in self._cfgstore:
            self._cfgstore['nodes'] = {}
        for node in self._cfgstore['nodes'].keys():
            if node not in nodes and 'groups' in self._cfgstore['nodes'][node]:
                self._cfgstore['nodes'][node]['groups'].discard(group)
        for node in nodes:
            if node not in self._cfgstore['nodes']:
                self._cfgstore['nodes'][node] = {'name': {'value': node},
                                                 'groups': set([group]) }
            elif 'groups' not in self._cfgstore['nodes'][node]:
                self._cfgstore['nodes'][node]['groups'] = set([group])
            else:
                self._cfgstore['nodes'][node]['groups'].add(group)

    def set_node_attributes(self, attribmap):
        if 'nodes' not in self._cfgstore:
            self._cfgstore['nodes'] = {}
        # TODO(jbjohnso): multi mgr support, here if we have peers,
        # pickle the arguments and fire them off in eventlet
        # flows to peers, all should have the same result
        for node in attribmap.keys():
            if node not in self._cfgstore['nodes']:
                self._cfgstore['nodes'][node] = {'name': {'value': node}}
            cfgobj = self._cfgstore['nodes'][node]
            exprmgr = _ExpressionFormat(cfgobj)
            recalcexpressions = False
            for attrname in attribmap[node].keys():
                newdict = {}
                if (isinstance(attribmap[node][attrname], dict) or
                        isinstance(attribmap[node][attrname], set)):
                    newdict = attribmap[node][attrname]
                else:
                    newdict = {'value': attribmap[node][attrname] }
                if attrname == 'groups':
                    self._sync_groups_to_node(node=node,
                    groups=attribmap[node]['groups'])
                if 'value' in newdict and attrname.startswith("secret."):
                    newdict['cryptvalue' ] = \
                        confluent.crypto.crypt_value(newdict['value'])
                    del newdict['value']
                cfgobj[attrname] = newdict
                if ('_expressionkeys' in cfgobj and
                        attrname in cfgobj['_expressionkeys']):
                    recalcexpressions = True
                if 'expression' in cfgobj[attrname]:  # evaluate now
                    cfgobj[attrname] = _decode_attribute(attrname, cfgobj,
                                                         formatter=exprmgr)
            if recalcexpressions:
                exprmgr = _ExpressionFormat(cfgobj)
                self._recalculate_expressions(cfgobj, formatter=exprmgr)
        self._bg_sync_to_file()
        #TODO: wait for synchronization to suceed/fail??)

    @classmethod
    def _read_from_file(cls):
        global _cfgstore
        nhandle = open(cls._cfgfilename, 'rb')
        fcntl.lockf(nhandle, fcntl.LOCK_SH)
        _cfgstore = cPickle.load(nhandle)
        fcntl.lockf(nhandle, fcntl.LOCK_UN)

    @classmethod
    def _bg_sync_to_file(cls):
        if cls._writepending:
            # already have a write scheduled
            return
        elif cls._cfgwriter is not None:
            #write in progress, request write when done
            cls._writepending = True
        else:
            cls._cfgwriter = eventlet.spawn(cls._sync_to_file)
            cls._cfgwriter.link(cls._write_complete)

    @classmethod
    def _write_complete(cls, gt):
        if cls._writepending:
            cls._writepending = False
            cls._cfgwriter = eventlet.spawn(cls._sync_to_file)
            cls._cfgwriter.link(cls._write_complete)
        else:
            cls._cfgwriter = None

    @classmethod
    def _sync_to_file(cls):
        # TODO: this is a pretty nasty performance penalty to pay
        # as much as it is mitigated and deferred, still need to do better
        # possibilities include:
        # threading this out (take it out of greenthread), GIL in theory should
        # not penalize this sort of threading, able to hide a great deal of
        # sins there...
        # doing dbm for the major trees, marking the objects that need update
        # and only doing changes for those
        # the in memory facet seems serviceable though
        nfn = cls._cfgfilename + '.new'
        nhandle = open(nfn, 'wb')
        fcntl.lockf(nhandle, fcntl.LOCK_EX)
        cPickle.dump(_cfgstore, nhandle, protocol=2)
        fcntl.lockf(nhandle, fcntl.LOCK_UN)
        nhandle.close()
        os.rename(cls._cfgfilename, cls._cfgfilename + '.old')
        os.rename(nfn, cls._cfgfilename)

    def _recalculate_expressions(self, cfgobj, formatter):
        for key in cfgobj.keys():
            if not isinstance(cfgobj[key],dict):
                continue
            if 'expression' in cfgobj[key]:
                cfgobj[key] = _decode_attribute(key, cfgobj,
                                                formatter=formatter)
            elif ('cryptvalue' not in cfgobj[key] and
                    'value' not in cfgobj[key]):
                # recurse for nested structures, with some hint tha
                # it might indeed be a nested structure
                _recalculate_expressions(cfgobj[key], formatter)
