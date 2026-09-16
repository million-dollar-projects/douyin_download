#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
abogus.py - Pure Python implementation of the a_bogus signature algorithm for Douyin Web API.
Zero external cryptographic dependencies (built-in SM3, RC4, Browser Fingerprints).
Based on reverse engineering of Douyin Web security protocols.
"""

import time
import random
from typing import Union, List, Dict, Tuple


# ==========================================
# Pure Python SM3 Implementation (GB/T 32918-2016)
# ==========================================

def _sm3_hash(msg: bytes) -> str:
    """Computes the 256-bit SM3 cryptographic hash of the input bytes, returning a 64-character lowercase hex string."""
    def rotl(x: int, n: int) -> int:
        return ((x << (n % 32)) & 0xFFFFFFFF) | ((x & 0xFFFFFFFF) >> (32 - (n % 32)))

    def P0(x: int) -> int:
        return x ^ rotl(x, 9) ^ rotl(x, 17)

    def P1(x: int) -> int:
        return x ^ rotl(x, 15) ^ rotl(x, 23)

    def FF(j: int, x: int, y: int, z: int) -> int:
        if 0 <= j <= 15:
            return x ^ y ^ z
        return (x & y) | (x & z) | (y & z)

    def GG(j: int, x: int, y: int, z: int) -> int:
        if 0 <= j <= 15:
            return x ^ y ^ z
        return (x & y) | ((~x) & z)

    def T(j: int) -> int:
        return 0x79CC4519 if 0 <= j <= 15 else 0x7A879D8A

    msg_len = len(msg) * 8
    pad = bytearray(msg)
    pad.append(0x80)
    while (len(pad) * 8) % 512 != 448:
        pad.append(0x00)
    pad.extend(msg_len.to_bytes(8, byteorder='big'))

    V = [
        0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
        0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E
    ]

    for i in range(0, len(pad), 64):
        block = pad[i:i+64]
        W = [0] * 68
        W1 = [0] * 64
        for t in range(16):
            W[t] = int.from_bytes(block[t*4:(t+1)*4], byteorder='big')
        for t in range(16, 68):
            W[t] = P1(W[t-16] ^ W[t-9] ^ rotl(W[t-3], 15)) ^ rotl(W[t-13], 7) ^ W[t-6]
        for t in range(64):
            W1[t] = W[t] ^ W[t+4]

        A, B, C, D, E, F, G, H = V
        for j in range(64):
            SS1 = rotl((rotl(A, 12) + E + rotl(T(j), j % 32)) & 0xFFFFFFFF, 7)
            SS2 = SS1 ^ rotl(A, 12)
            TT1 = (FF(j, A, B, C) + D + SS2 + W1[j]) & 0xFFFFFFFF
            TT2 = (GG(j, E, F, G) + H + SS1 + W[j]) & 0xFFFFFFFF
            D = C
            C = rotl(B, 9)
            B = A
            A = TT1
            H = G
            G = rotl(F, 19)
            F = E
            E = P0(TT2)

        V = [
            A ^ V[0], B ^ V[1], C ^ V[2], D ^ V[3],
            E ^ V[4], F ^ V[5], G ^ V[6], H ^ V[7]
        ]

    return ''.join(f'{x:08x}' for x in V)


# ==========================================
# String & Byte Utilities
# ==========================================

class StringProcessor:
    @staticmethod
    def to_ord_str(s: str) -> str:
        return "".join([chr(i) for i in s])

    @staticmethod
    def to_ord_array(s: str) -> List[int]:
        return [ord(char) for char in s]

    @staticmethod
    def to_char_str(s: Union[List[int], str]) -> str:
        return "".join([chr(i) if isinstance(i, int) else i for i in s])

    @staticmethod
    def to_char_array(s: str) -> List[int]:
        return [ord(char) for char in s]

    @staticmethod
    def js_shift_right(val: int, n: int) -> int:
        return (val % 0x100000000) >> n

    @staticmethod
    def generate_random_bytes(length: int = 3) -> str:
        def generate_byte_sequence() -> List[str]:
            _rd = int(random.random() * 10000)
            return [
                chr(((_rd & 255) & 170) | 1),
                chr(((_rd & 255) & 85) | 2),
                chr((StringProcessor.js_shift_right(_rd, 8) & 170) | 5),
                chr((StringProcessor.js_shift_right(_rd, 8) & 85) | 40),
            ]

        result = []
        for _ in range(length):
            result.extend(generate_byte_sequence())
        return "".join(result)


# ==========================================
# Cryptographic Utilities
# ==========================================

ORIGINAL_BIG_ARRAY = [
    121, 243,  55, 234, 103,  36,  47, 228,  30, 231, 106,   6, 115,  95,  78, 101, 250, 207, 198,  50,
    139, 227, 220, 105,  97, 143,  34,  28, 194, 215,  18, 100, 159, 160,  43,   8, 169, 217, 180, 120,
    247,  45,  90,  11,  27, 197,  46,   3,  84,  72,   5,  68,  62,  56, 221,  75, 144,  79,  73, 161,
    178,  81,  64, 187, 134, 117, 186, 118,  16, 241, 130,  71,  89, 147, 122, 129,  65,  40,  88, 150,
    110, 219, 199, 255, 181, 254,  48,   4, 195, 248, 208,  32, 116, 165,   2, 190,  69,  96, 185, 201,
    156, 212,  19, 175, 192, 245, 124, 108,  29,  17, 172, 125,  63, 168, 158, 151, 218, 102, 177,  76,
    188, 107, 249, 149, 152,  33, 238,   1, 157, 164, 148, 179, 216,  74,  83, 154, 253, 213,  99,  15,
     39, 141, 196, 171, 114,  61, 166, 184, 127,  60, 239, 202, 132, 189, 224,  44, 136, 174, 146, 244,
    128, 162, 225,  54, 229,  87, 232, 112, 155, 176, 251, 211, 210, 170,  52,  82, 137, 226,  38, 237,
    135,  26, 182, 126,  59, 119,  41, 205, 111, 191, 167, 131, 140,  23,  25,  86, 173, 242, 142, 206,
    222, 235, 236,  77,  37, 138, 104, 200, 223, 252, 193,  57,  70, 123, 145, 214, 153,  31,  24, 240,
    204, 230,  98,  67,  85,  14, 133,  49,  94,  92,  53,  80, 203, 183,  91,  35, 163, 209,  58, 233,
      9,  12, 109, 148,  20, 228, 113,  66,  42, 120,   7,  21, 147, 105, 246,  93, 215, 139, 115,  51,
     22,  13, 114, 146, 117, 216,  78,  34,  73, 106,  10, 247, 121, 159,  95, 231,  30, 243,  79, 130,
    161, 220, 103,  28, 116, 160, 137, 177,  38, 118,  16,  27, 129, 150, 255,  55,  63, 241, 207, 186,
     18, 227,  97,  36, 112, 169, 170,  75, 180, 122,  71,  11, 144,  65,  47, 254,  84, 221,  72, 187,
    134,  64,  89,  56,  90, 158, 218, 188,  52, 181,  88,  45,  81,  40, 198, 250,  46, 219, 197,  43,
      0, 234, 101,  33, 110, 179, 174, 249, 119, 154,   5,   8,  74,  48,   4, 238, 199, 138, 202, 184,
    194, 217,  99, 143,  87, 164, 152,  76, 210, 141,  68,  17, 149, 102,  39, 248, 195,  82, 132, 189,
    208, 212, 100,  32, 182, 175, 136, 171, 126, 145, 128, 125,  19, 108,  50, 245, 156, 211,  69, 185,
    201, 215,  14, 133,  86, 172, 127, 200, 107, 140, 222,  23,  96, 124,  29, 244, 192, 162, 223, 190,
    205, 225,  67, 109,  49, 168, 183, 235,  24, 153, 240, 209, 123,  62,  44, 224, 178, 232, 166,  54,
    239, 213,  94, 148, 135, 167, 173,  77, 203, 163,  60,  85,  70,  92,  15, 229, 196, 226, 131,  61,
    206, 242,  98,  26, 111, 165, 142,  37,  58, 113,  91,  25,  53,  80,  66, 237, 236,  57, 191, 193,
    214, 230,  83, 155,  59, 176, 137, 227, 115, 160, 252,  31,  65, 105,  42, 228, 198, 251, 177, 246,
    104, 233,  12, 139, 110, 174, 180,  78,  30, 147,  46,  16,  72, 117,  55, 255,   1, 243,  79, 186,
     36, 221,  75, 118,   2, 169, 170, 218, 188, 120,   6,  27,  84,  40, 101, 254,  89, 234, 103,  47,
    197,  50, 100, 122,  71, 161, 144, 219, 119, 150,  35, 112,  74,  90,  43, 247, 199,  88, 187, 184,
    194, 216,  97,  34,  87, 162, 134,  76, 141, 116,  68, 130,  81,  45,  39, 240, 195,  82, 189, 201,
    212, 220, 106, 143, 114, 175, 152, 171, 126, 154,  69, 125,  19,  48,   4, 245, 156, 211, 185, 207,
    132, 217,  18, 107,  86, 164, 127, 200, 181, 140, 222,  23,  96, 124,  29, 238, 192, 173, 223, 190,
    205, 232,  67, 113,  49, 167, 183, 235,  24, 153, 204, 209,  53,  62,  44, 229, 178, 226, 166,  54,
    239, 213,  94,  33, 135, 165, 136,  77, 203, 163,  60,  85,  70,  92,  15, 248, 196, 210, 131,  61,
    206, 249,  98,  26, 111, 168, 142,  37,  58, 109,  91,  25,  80,  66,  14, 237, 236,  57, 191, 193,
    214, 230,  83, 158,  59, 176,  52, 225,  41, 129, 252,  31, 123, 145,  38, 228, 198, 250, 176, 241,
     95, 233,  12, 105, 110, 174, 180,  75,  30, 147,  46,  16,  72, 122,  55, 255,   1, 243,  79, 186,
     36, 220,  73, 118,   2, 169, 170, 218, 188, 120,   6,  27,  84,  40,  99, 254,  89, 234, 103,  47,
    197,  50,  97, 121,  71, 161, 144, 219, 119, 150,  35, 112,  74,  90,  43, 247, 199,  88, 187, 184,
    194, 216, 100,  34,  87, 162, 134,  76, 141, 116,  68, 130,  81,  45,  39, 240, 195,  82, 189, 201,
    212, 227, 106, 143, 114, 175, 152, 171, 126, 154,  69, 125,  19,  48,   4, 245, 156, 211, 185, 207,
    132, 215,  18, 107,  86, 164, 127, 200, 181, 140, 222,  23,  96, 124,  29, 238, 192, 173, 223, 190,
    205, 232,  67, 113,  49, 167, 183, 235,  24, 153, 204, 209,  53,  62,  44, 229, 178, 226, 166,  54,
    239, 213,  94,  33, 135, 165, 136,  77, 203, 163,  60,  85,  70,  92,  15, 248, 196, 210, 131,  61,
    206, 249,  98,  26, 111, 168, 142,  37,  58, 109,  91,  25,  80,  66,  14, 237, 236,  57, 191, 193,
    214, 230,  83, 158,  59, 176,  52, 225,  41, 129, 252,  31, 123, 145,  38, 228
]


class CryptoUtility:
    def __init__(self, salt: str, custom_base64_alphabet: List[str]):
        self.salt = salt
        self.base64_alphabet = custom_base64_alphabet

    @staticmethod
    def sm3_to_array(input_data: Union[str, List[int], bytes]) -> List[int]:
        if isinstance(input_data, str):
            input_data_bytes = input_data.encode("utf-8")
        elif isinstance(input_data, list):
            input_data_bytes = bytes(input_data)
        else:
            input_data_bytes = input_data

        hex_result = _sm3_hash(input_data_bytes)
        return [int(hex_result[i:i+2], 16) for i in range(0, len(hex_result), 2)]

    def add_salt(self, param: str) -> str:
        return param + self.salt

    def process_param(self, param: Union[str, List[int]], add_salt: bool) -> Union[str, List[int]]:
        if isinstance(param, str) and add_salt:
            param = self.add_salt(param)
        return param

    def params_to_array(self, param: Union[str, List[int]], add_salt: bool = True) -> List[int]:
        processed_param = self.process_param(param, add_salt)
        return self.sm3_to_array(processed_param)

    def transform_bytes(self, bytes_list: List[int]) -> str:
        big_array = list(ORIGINAL_BIG_ARRAY)
        bytes_str = StringProcessor.to_char_str(bytes_list)
        result_str = []
        index_b = big_array[1]
        initial_value = 0
        value_e = 0

        for index, char in enumerate(bytes_str):
            if index == 0:
                initial_value = big_array[index_b]
                sum_initial = index_b + initial_value
                big_array[1] = initial_value
                big_array[index_b] = index_b
            else:
                sum_initial = initial_value + value_e

            char_value = ord(char)
            sum_initial %= len(big_array)
            value_f = big_array[sum_initial]
            encrypted_char = char_value ^ value_f
            result_str.append(chr(encrypted_char))

            value_e = big_array[(index + 2) % len(big_array)]
            sum_initial = (index_b + value_e) % len(big_array)
            initial_value = big_array[sum_initial]
            big_array[sum_initial] = big_array[(index + 2) % len(big_array)]
            big_array[(index + 2) % len(big_array)] = initial_value
            index_b = sum_initial

        return "".join(result_str)

    def base64_encode(self, input_string: str, selected_alphabet: int = 0) -> str:
        binary_string = "".join(["{:08b}".format(ord(char)) for char in input_string])
        padding_length = (6 - len(binary_string) % 6) % 6
        binary_string += "0" * padding_length

        base64_indices = [
            int(binary_string[i : i + 6], 2) for i in range(0, len(binary_string), 6)
        ]
        output_string = "".join(
            [self.base64_alphabet[selected_alphabet][index] for index in base64_indices]
        )
        output_string += "=" * (padding_length // 2)
        return output_string

    def abogus_encode(self, abogus_bytes_str: str, selected_alphabet: int) -> str:
        abogus = []
        for i in range(0, len(abogus_bytes_str), 3):
            if i + 2 < len(abogus_bytes_str):
                n = (
                    (ord(abogus_bytes_str[i]) << 16)
                    | (ord(abogus_bytes_str[i + 1]) << 8)
                    | ord(abogus_bytes_str[i + 2])
                )
            elif i + 1 < len(abogus_bytes_str):
                n = (ord(abogus_bytes_str[i]) << 16) | (ord(abogus_bytes_str[i + 1]) << 8)
            else:
                n = ord(abogus_bytes_str[i]) << 16

            for j, k in zip(range(18, -1, -6), (0xFC0000, 0x03F000, 0x0FC0, 0x3F)):
                if j == 6 and i + 1 >= len(abogus_bytes_str):
                    break
                if j == 0 and i + 2 >= len(abogus_bytes_str):
                    break
                abogus.append(self.base64_alphabet[selected_alphabet][(n & k) >> j])

        abogus.append("=" * ((4 - len(abogus) % 4) % 4))
        return "".join(abogus)

    @staticmethod
    def rc4_encrypt(key: bytes, plaintext: str) -> bytes:
        S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) % 256
            S[i], S[j] = S[j], S[i]

        i = j = 0
        ciphertext = []
        for char in plaintext:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            K = S[(S[i] + S[j]) % 256]
            ciphertext.append(ord(char) ^ K)

        return bytes(ciphertext)


# ==========================================
# Browser Fingerprint Generator
# ==========================================

class BrowserFingerprintGenerator:
    @staticmethod
    def generate_fingerprint(browser_type: str = "Edge") -> str:
        width = 1920
        height = 1080
        avail_width = 1920
        avail_height = 1040
        color_depth = 24
        pixel_depth = 24

        if browser_type.lower() in ["edge", "chrome"]:
            plugins_len = random.choice([3, 4, 5])
        elif browser_type.lower() == "firefox":
            plugins_len = 0
        else:
            plugins_len = 3

        timezone_offset = -480
        inner_width = 1920
        inner_height = 920
        outer_width = 1920
        outer_height = 1040
        screen_x = 0
        screen_y = 0

        return f"{width}|{height}|{avail_width}|{avail_height}|{color_depth}|{pixel_depth}|{plugins_len}|{timezone_offset}|{inner_width}|{inner_height}|{outer_width}|{outer_height}|{screen_x}|{screen_y}"


# ==========================================
# ABogus Main Signer Class
# ==========================================

class ABogus:
    """ABogus signature calculation engine for Douyin / TikTok Web API."""

    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        fp: str = "",
        aid: int = 6383,
        page_id: int = 0
    ):
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.browser_fp = fp or BrowserFingerprintGenerator.generate_fingerprint("Edge")
        self.aid = aid
        self.pageId = page_id

        self.salt = "s3"
        self.custom_base64_alphabet = [
            "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
            "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
            "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
            "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe",
        ]

        self.crypto_utility = CryptoUtility(self.salt, self.custom_base64_alphabet)

        self.sort_index = [
            44, 20, 56, 45, 21, 57, 46, 22, 58, 47, 23, 59, 48, 0, 60, 49,
            1, 61, 50, 2, 62, 51, 3, 63, 52, 4, 64, 53, 5, 65, 54, 6,
            66, 55, 7, 67, 8, 68, 9, 69, 10, 70, 11, 71, 12, 72, 13, 73,
            14, 74, 15, 75, 16, 76, 17, 77, 18, 78, 19, 79, 24, 80, 25, 81,
            26, 82, 27, 83, 28, 84, 29, 85, 30, 86, 31, 87, 32, 88, 33, 89,
            34, 90, 35, 91, 36, 92, 37, 93, 38, 94, 39, 95, 40, 96, 41, 97,
            42, 98, 43, 99
        ]

        self.sort_index_2 = [
            44, 20, 45, 21, 46, 22, 47, 23, 48, 0, 49, 1, 50, 2, 51, 3,
            52, 4, 53, 5, 54, 6, 55, 7, 8, 9, 10, 11, 12, 13, 14, 15,
            16, 17, 18, 19, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
            36, 37, 38, 39, 40, 41, 42, 43, 64, 65, 56, 57, 58, 59, 60, 61,
            62, 63, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79,
            80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95,
            96, 97, 98, 99
        ]

    def generate_abogus(self, params: str, body: str = "") -> Tuple[str, str, str, str]:
        """Calculates a_bogus signature for query params and body.
        
        Returns:
            Tuple of (signed_params_string, abogus_token, user_agent, body)
        """
        # Phase 1: Hash query params and body
        params_array = self.crypto_utility.params_to_array(params, add_salt=True)
        body_array = self.crypto_utility.params_to_array(body, add_salt=True)

        # Phase 2: RC4 encrypt User-Agent with salt
        rc4_key = bytes([ord(c) for c in self.salt])
        ua_encrypted = self.crypto_utility.rc4_encrypt(rc4_key, self.user_agent)
        ua_b64 = self.crypto_utility.base64_encode(StringProcessor.to_char_str(list(ua_encrypted)), 1)
        ua_array = self.crypto_utility.sm3_to_array(ua_b64)

        # Phase 3: Construct 100-byte structure
        ab_dir: Dict[int, int] = {}
        now_ms = int(time.time() * 1000)
        ab_dir[0] = (now_ms >> 24) & 255
        ab_dir[1] = (now_ms >> 16) & 255
        ab_dir[2] = (now_ms >> 8) & 255
        ab_dir[3] = now_ms & 255

        ab_dir[4] = 64
        ab_dir[5] = 1
        ab_dir[6] = 0
        ab_dir[7] = 8
        ab_dir[8] = 0

        for i in range(len(params_array)):
            ab_dir[9 + i] = params_array[i]

        for i in range(len(body_array)):
            ab_dir[41 + i] = body_array[i]

        for i in range(len(ua_array)):
            ab_dir[73 + i] = ua_array[i]

        ab_dir[51] = (self.pageId >> 24) & 255
        ab_dir[52] = (self.pageId >> 16) & 255
        ab_dir[53] = (self.pageId >> 8) & 255
        ab_dir[54] = self.pageId & 255
        ab_dir[55] = self.pageId & 255
        ab_dir[56] = self.aid & 255
        ab_dir[57] = self.aid & 255
        ab_dir[58] = (self.aid >> 8) & 255
        ab_dir[59] = (self.aid >> 16) & 255
        ab_dir[60] = (self.aid >> 24) & 255

        ab_dir[64] = len(self.browser_fp) & 255
        ab_dir[65] = len(self.browser_fp) & 255

        sorted_values = [ab_dir.get(i, 0) for i in self.sort_index]
        edge_fp_array = StringProcessor.to_char_array(self.browser_fp)

        ab_xor = 0
        for index in range(len(self.sort_index_2) - 1):
            if index == 0:
                ab_xor = ab_dir.get(self.sort_index_2[index], 0)
            ab_xor ^= ab_dir.get(self.sort_index_2[index + 1], 0)

        sorted_values.extend(edge_fp_array)
        sorted_values.append(ab_xor)

        abogus_bytes_str = (
            StringProcessor.generate_random_bytes()
            + self.crypto_utility.transform_bytes(sorted_values)
        )

        abogus = self.crypto_utility.abogus_encode(abogus_bytes_str, 0)
        signed_params = f"{params}&a_bogus={abogus}"
        return (signed_params, abogus, self.user_agent, body)


def generate_a_bogus(params: str, user_agent: str = ABogus.DEFAULT_USER_AGENT, body: str = "") -> str:
    """Generates the a_bogus string token for given params and User-Agent."""
    signer = ABogus(user_agent=user_agent)
    _, abogus, _, _ = signer.generate_abogus(params=params, body=body)
    return abogus
