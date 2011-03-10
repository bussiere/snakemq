# -*- coding: utf-8 -*-
"""
Data format
===========
Each packet contains always a single frame: C{[1B type|payload]}.

Payload
-------
- protokol version: C{[4B version]}.
- incompatible protocol: C{[]}
- identification: C{[ident]}
- message: C{[16B UUID|4B TTL|4B flags|message]}

@author: David Siroky (siroky@dasir.cz)
"""

import struct
import logging
import threading
import uuid

from snakemq.exceptions import SnakeMQBrokenMessage, SnakeMQException
from snakemq.queues import QueuesManager, Message
import snakemq.version

############################################################################
############################################################################

FRAME_TYPE_PROTOCOL_VERSION = "\x00"
FRAME_TYPE_INCOMPATIBLE_PROTOCOL = "\x01"
FRAME_TYPE_IDENTIFICATION = "\x02"
FRAME_TYPE_MESSAGE = "\x03"

FRAME_FORMAT_PROTOCOL_VERSION = "!I"
FRAME_FORMAT_PROTOCOL_VERSION_SIZE = struct.calcsize(FRAME_FORMAT_PROTOCOL_VERSION)
FRAME_FORMAT_MESSAGE = "!16sII"
FRAME_FORMAT_MESSAGE_SIZE = struct.calcsize(FRAME_FORMAT_MESSAGE)

MIN_FRAME_SIZE = 1 # just the type field

MESSAGE_FLAG_PERSISTENT = 0x1 #: deliver at all cost (queue to disk as well)

#############################################################################
#############################################################################

class Messaging(object):
    def __init__(self, identifier, domain, packeter, queues_storage=None):
        self.identifier = identifier
        self.domain = domain
        self.packeter = packeter
        self.queues_manager = QueuesManager(queues_storage)
        self.log = logging.getLogger("snakemq.messaging")

        # callbacks
        self.on_error = None #: C{func(conn_id, exception)}
        self.on_message_recv = None #: C{func(conn_id, ident, message)}

        self._ident_by_conn = {}
        self._conn_by_ident = {}

        packeter.link.on_loop_pass = self.on_link_loop_pass
        packeter.on_connect = self._on_connect
        packeter.on_disconnect = self._on_disconnect
        packeter.on_packet_recv = self._on_packet_recv

        self._lock = threading.Lock()

    ###########################################################

    def _on_connect(self, conn_id):
        self.send_protocol_version(conn_id)
        self.send_identification(conn_id)

    ###########################################################

    def _on_disconnect(self, conn_id):
        if conn_id in self._ident_by_conn:
            ident = self._ident_by_conn.pop(conn_id)
            with self._lock:
                self.queues_manager.get_queue(ident).disconnect()
            del self._conn_by_ident[ident]

    ###########################################################

    def parse_protocol_version(self, payload, conn_id):
        if len(payload) != FRAME_FORMAT_PROTOCOL_VERSION_SIZE:
            raise SnakeMQBrokenMessage("protocol version")

        protocol = struct.unpack(FRAME_FORMAT_PROTOCOL_VERSION, payload)[0]
        if protocol != snakemq.version.PROTOCOL_VERSION:
            self.send_incompatible_protocol(conn_id)
            raise SnakeMQIncompatibleProtocol(
                            "remote side protocol version is %i" % protocol)
        self.log.debug("conn=%s remote version %X" % (conn_id, protocol))

    ###########################################################

    def parse_incompatible_protocol(self, conn_id):
        self.log.debug("conn=%s remote side rejected protocol version" % conn_id)
        # TODO

    ###########################################################

    def parse_identification(self, remote_ident, conn_id):
        self.log.debug("conn=%s remote ident '%s'" % (conn_id, remote_ident))
        with self._lock:
            self.queues_manager.get_queue(remote_ident).connect()
        self._ident_by_conn[conn_id] = remote_ident
        self._conn_by_ident[remote_ident] = conn_id

    ###########################################################

    def parse_message(self, payload, conn_id):
        if len(payload) < FRAME_FORMAT_MESSAGE_SIZE:
            raise SnakeMQBrokenMessage("message")

        uuid, ttl, flags = struct.unpack(FRAME_FORMAT_MESSAGE,
                                          payload[:FRAME_FORMAT_MESSAGE_SIZE])
        if self.on_message_recv:
            message = Message(data=payload[FRAME_FORMAT_MESSAGE_SIZE:],
                              uuid=uuid, ttl=ttl, flags=flags)
            self.on_message_recv(conn_id, self._ident_by_conn[conn_id], message)

    ###########################################################

    def _on_packet_recv(self, conn_id, packet):
        try:
            if len(packet) < MIN_FRAME_SIZE:
                raise SnakeMQBrokenMessage("too small")

            frame_type = packet[0]
            payload = packet[1:]
            del packet

            # TODO allow parse_* calls only after protocol version negotiation
            if frame_type == FRAME_TYPE_PROTOCOL_VERSION:
                self.parse_protocol_version(payload, conn_id)
            elif frame_type == FRAME_TYPE_INCOMPATIBLE_PROTOCOL:
                self.parse_incompatible_protocol(conn_id)
            elif frame_type == FRAME_TYPE_IDENTIFICATION:
                self.parse_identification(payload, conn_id)
            elif frame_type == FRAME_TYPE_MESSAGE:
                self.parse_message(payload, conn_id)
        except SnakeMQException, exc:
            self.log.error("conn=%s ident=%s %r" % 
                  (conn_id, self._ident_by_conn.get(conn_id), exc))
            if self.on_error:
                self.on_error(conn_id, exc)
            self.packeter.link.close(conn_id)

    ###########################################################

    def send_protocol_version(self, conn_id):
        self.packeter.send_packet(conn_id,
            FRAME_TYPE_PROTOCOL_VERSION + 
            struct.pack(FRAME_FORMAT_PROTOCOL_VERSION,
                        snakemq.version.PROTOCOL_VERSION))

    ###########################################################

    def send_incompatible_protocol(self, conn_id):
        self.packeter.send_packet(conn_id,
            FRAME_TYPE_INCOMPATIBLE_PROTOCOL)

    ###########################################################

    def send_identification(self, conn_id):
        self.packeter.send_packet(conn_id,
            FRAME_TYPE_IDENTIFICATION + self.identifier)

    ###########################################################

    def send_message_frame(self, conn_id, message):
        self.packeter.send_packet(conn_id,
            FRAME_TYPE_MESSAGE +
            struct.pack(FRAME_FORMAT_MESSAGE,
                        message.uuid,
                        message.ttl,
                        message.flags) +
            message.data)

    ###########################################################

    def on_link_loop_pass(self):
        for ident, conn_id in self._conn_by_ident.items():
            with self._lock:
                queue = self.queues_manager.get_queue(ident)
                if len(queue) == 0:
                    continue
                item = queue.get()
                queue.pop()
                self.send_message_frame(conn_id, item)

    ###########################################################

    def send_message(self, ident, message):
        """
        @param ident: destination address
        @param message: L{Message}
        """
        assert isinstance(message, Message)
        with self._lock:
            self.queues_manager.get_queue(ident).push(message)
