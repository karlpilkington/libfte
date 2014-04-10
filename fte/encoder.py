#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of libfte.
#
# libfte is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# libfte is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with libfte.  If not, see <http://www.gnu.org/licenses/>.

import string
import math

import fte.conf
import fte.bit_ops
import fte.dfa
import fte.encrypter


class InsufficientCapacityException(Exception):
    """Raised when the language doesn't have enough capacity to support a payload"""
    pass


class InvalidInputException(Exception):

    """Raised when the input to ``fte.encoder.RegexEncoder.encode`` or
    ``fte.encoder.RegexEncoder.decode`` is not a string.
    """
    pass


class DecodeFailureError(Exception):

    """Raised when ``decode`` fails to properly recover a message.
    """
    pass


_instance = {}


class RegexEncoder(object):

    """A proxy object used for caching invocations of ``RegexEncoderObject``.
    If a ``RegexEncoder`` is invoked twice in one process, we want to invoke
    ``RegexEncoderObject.__init__`` only once.
    """

    def __new__(self, regex, fixed_slice):
        global _instance
        if not _instance.get((regex, fixed_slice)):
            _instance[(regex, fixed_slice)] = RegexEncoderObject(
                regex, fixed_slice)
        return _instance[(regex, fixed_slice)]


class RegexEncoderObject(object):
    _COVERTEXT_HEADER_LEN_PLAINTEXT = 8
    _COVERTEXT_HEADER_LEN_CIPHERTTEXT = 16

    def __init__(self, regex, fixed_slice):
        """Constructs a new object that can be used for encoding/decoding.
        The value ``regex`` is the regular epxression we will use and all messages
        will match that regex. The value ``fixed_slice`` is the subset of the language
        we will use for (un)ranking. That is, ``encode`` will output strings of the
        format ``unrank(X[:n]) || X[n:]``, where unrank(X[:n]) is always of length
        ``fixed_slice``.
        """
        self._regex = regex
        self._fixed_slice = fixed_slice
        self._dfa = fte.dfa.from_regex(self._regex, self._fixed_slice)
        self._encrypter = fte.encrypter.Encrypter()

    def getCapacity(self):
        """Returns the size, in bits, of the language of our input ``regex``.
        Calculated as the floor of log (base 2) of the cardinality of the set of
        strings up to length ``fixed_slice`` in the language generated by the input
        ``regex``.
        """

        return self._dfa._capacity

    def encode(self, X):
        """Given a string ``X``, returns ``unrank(X[:n]) || X[n:]`` where ``n``
        is the the maximum number of bytes that can be unranked w.r.t. the
        capacity of the input ``regex`` and ``unrank`` is w.r.t. to the input
        ``regex``.
        """

        if not isinstance(X, str):
            raise InvalidInputException('Input must be of type string.')

        maximumBytesToRank = int(math.floor(self.getCapacity() / 8.0))
        unrank_payload_len = (
            maximumBytesToRank - RegexEncoderObject._COVERTEXT_HEADER_LEN_CIPHERTTEXT)
        unrank_payload_len = min(len(X), unrank_payload_len)

        if unrank_payload_len <= 0:
            raise InsufficientCapacityException('Language doesn\'t have enough capacity')

        msg_len_header = fte.bit_ops.long_to_bytes(unrank_payload_len)
        msg_len_header = string.rjust(
            msg_len_header, RegexEncoderObject._COVERTEXT_HEADER_LEN_PLAINTEXT, '\x00')
        msg_len_header = fte.bit_ops.random_bytes(8) + msg_len_header
        msg_len_header = self._encrypter.encryptOneBlock(msg_len_header)

        unrank_payload = msg_len_header + \
            X[:maximumBytesToRank -
                RegexEncoderObject._COVERTEXT_HEADER_LEN_CIPHERTTEXT]

        random_padding_bytes = maximumBytesToRank - len(unrank_payload)
        if random_padding_bytes > 0:
            unrank_payload += fte.bit_ops.random_bytes(random_padding_bytes)

        unrank_payload = fte.bit_ops.bytes_to_long(unrank_payload)

        formatted_covertext_header = self._dfa.unrank(unrank_payload)
        unformatted_covertext_body = X[
            maximumBytesToRank - RegexEncoderObject._COVERTEXT_HEADER_LEN_CIPHERTTEXT:]

        covertext = formatted_covertext_header + unformatted_covertext_body

        return covertext

    def decode(self, covertext):
        """Given an input string ``unrank(X[:n]) || X[n:]`` returns ``X``.
        """

        if not isinstance(covertext, str):
            raise InvalidInputException('Input must be of type string.')

        insufficient = (len(covertext) < self._fixed_slice)
        if insufficient:
            raise DecodeFailureError(
                "Covertext is shorter than self._fixed_slice, can't decode.")

        maximumBytesToRank = int(math.floor(self.getCapacity() / 8.0))

        rank_payload = self._dfa.rank(covertext[:self._fixed_slice])
        X = fte.bit_ops.long_to_bytes(rank_payload)

        X = string.rjust(X, maximumBytesToRank, '\x00')
        msg_len_header = self._encrypter.decryptOneBlock(
            X[:RegexEncoderObject._COVERTEXT_HEADER_LEN_CIPHERTTEXT])
        msg_len_header = msg_len_header[8:16]
        msg_len = fte.bit_ops.bytes_to_long(
            msg_len_header[:RegexEncoderObject._COVERTEXT_HEADER_LEN_PLAINTEXT])

        retval = X[16:16 + msg_len]
        retval += covertext[self._fixed_slice:]

        return retval
