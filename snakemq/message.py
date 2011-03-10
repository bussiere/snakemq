# -*- coding: utf-8 -*-
"""
Message container.
"""

import uuid as uuid_module

###########################################################################
###########################################################################

FLAG_PERSISTENT = 0x1 # store to a persistent storage

###########################################################################
###########################################################################

class Message(object):
    def __init__(self, data, uuid=None, ttl=0, flags=0):
        self.data = data
        self.uuid = uuid or uuid_module.uuid1().bytes
        self.ttl = ttl
        self.flags = flags

    def __repr__(self):
        return "<%s id=%X uuid=%r ttl=%f len=%i>" % (
            self.__class__.__name__, id(self), self.uuid,
            self.ttl, len(self.data))
