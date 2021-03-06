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

import base64
import json


class ConfluentException(Exception):
    apierrorcode = 500
    apierrorstr = 'Unexpected Error'

    def get_error_body(self):
        return self.apierrorstr


class NotFoundException(ConfluentException):
    # Something that could be construed as a name was not found
    # basically, picture an http error code 404
    pass


class InvalidArgumentException(ConfluentException):
    # Something from the remote client wasn't correct
    # like http code 400
    pass


class TargetEndpointUnreachable(ConfluentException):
    # A target system was unavailable.  For example, a BMC
    # was unreachable.  http code 504
    pass


class TargetEndpointBadCredentials(ConfluentException):
    # target was reachable, but authentication/authorization
    # failed
    pass


class LockedCredentials(ConfluentException):
    # A request was performed that required a credential, but the credential
    # store is locked
    pass


class ForbiddenRequest(ConfluentException):
    # The client request is not allowed by authorization engine
    pass


class NotImplementedException(ConfluentException):
    # The current configuration/plugin is unable to perform
    # the requested task. http code 501
    pass


class GlobalConfigError(ConfluentException):
    # The configuration in the global config file is not right
    pass


class PubkeyInvalid(ConfluentException):
    apierrorcode = 502
    apierrorstr = '502 - Invalid certificate or key on target'

    def __init__(self, text, certificate, fingerprint, attribname, event):
        super(PubkeyInvalid, self).__init__(self, text)
        self.fingerprint = fingerprint
        bodydata = {'message': text,
                    'event': event,
                    'fingerprint': fingerprint,
                    'fingerprintfield': attribname,
                    'certificate': base64.b64encode(certificate)}
        self.errorbody = json.dumps(bodydata)

    def get_error_body(self):
        return self.errorbody

class LoggedOut(ConfluentException):
    apierrorcode = 401
    apierrorstr = '401 - Logged out'

    def get_error_body(self):
        return '{"loggedout": 1}'
