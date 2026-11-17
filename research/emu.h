// Minimal standalone adapter for MAME's BSD-3-Clause eDSP disassembler.
#pragma once
#include <cstdint>
#include <cstdio>
#include <ostream>
#include <vector>
using u8 = uint8_t;
using u16 = uint16_t;
using u32 = uint32_t;
using offs_t = uint32_t;
inline unsigned BIT(unsigned v, unsigned s, unsigned n=1) { return (v >> s) & ((1u << n)-1); }
namespace util {
inline int32_t sext(uint32_t n, unsigned bits) { return int32_t(n << (32-bits)) >> (32-bits); }
template<typename... Args> void stream_format(std::ostream& s, const char *fmt, Args... a) {
    char b[512]; std::snprintf(b, sizeof(b), fmt, a...); s << b;
}
struct disasm_interface {
    static constexpr u32 SUPPORTED=0x80000000, STEP_COND=0x10000000,
                         STEP_OVER=0x20000000, STEP_OUT=0x40000000;
    struct data_buffer {
        std::vector<u8> bytes;
        u16 r16(offs_t word) const {
            auto off = word*2;
            return off+1 < bytes.size() ? u16(bytes[off] | bytes[off+1]<<8) : 0;
        }
    };
};
}
