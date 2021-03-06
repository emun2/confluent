# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2014 IBM Corporation
# Copyright 2015 Lenovo
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Various utility functions that do not neatly fit into one category or another
import base64
import confluent.exceptions as cexc
import confluent.log as log
import hashlib
import os
import struct


def randomstring(length=20):
    """Generate a random string of requested length

    :param length: The number of characters to produce, defaults to 20
    """
    chunksize = length / 4
    if length % 4 > 0:
        chunksize += 1
    strval = base64.urlsafe_b64encode(os.urandom(chunksize * 3))
    return strval[0:length-1]


def securerandomnumber(low=0, high=4294967295):
    """Return a random number within requested range

    Note that this function will not return smaller than 0 nor larger
    than 2^32-1 no matter what is requested.
    The python random number facility does not provide characteristics
    appropriate for secure rng, go to os.urandom

    :param low: Smallest number to return (defaults to 0)
    :param high: largest number to return (defaults to 2^32-1)
    """
    number = -1
    while number < low or number > high:
        number = struct.unpack("I", os.urandom(4))[0]
    return number


def monotonic_time():
    """Return a monotoc time value

    In scenarios like timeouts and such, monotonic timing is preferred.
    """
    # for now, just support POSIX systems
    return os.times()[4]

class TLSCertVerifier(object):
    def __init__(self, configmanager, node, fieldname):
        self.cfm = configmanager
        self.node = node
        self.fieldname = fieldname

    def verify_cert(self, certificate):
        fingerprint = 'sha512$' + hashlib.sha512(certificate).hexdigest()
        storedprint = self.cfm.get_node_attributes(self.node, (self.fieldname,)
                                                   )
        if self.fieldname not in storedprint[self.node]:  # no stored value, check
                                                   # policy for next action
            newpolicy = self.cfm.get_node_attributes(self.node,
                                                     ('pubkeys.addpolicy',))
            if ('pubkeys.addpolicy' in newpolicy[self.node] and
                    'value' in newpolicy[self.node]['pubkeys.addpolicy'] and
                    newpolicy[self.node]['pubkeys.addpolicy']['value'] == 'manual'):
                # manual policy means always raise unless a match is set
                # manually
                raise cexc.PubkeyInvalid('New certificate detected',
                                         certificate, fingerprint,
                                         self.fieldname, 'newkey')
            # since the policy is not manual, go ahead and add new key
            # after logging to audit log
            auditlog = log.Logger('audit')
            auditlog.log({'node': self.node, 'event': 'certautoadd',
                          'fingerprint': fingerprint})
            self.cfm.set_node_attributes(
                {self.node: {self.fieldname: fingerprint}})
            return True
        elif storedprint[self.node][self.fieldname]['value'] == fingerprint:
            return True
        raise cexc.PubkeyInvalid(
            'Mismatched certificate detected', certificate, fingerprint,
            self.fieldname, 'mismatch')
