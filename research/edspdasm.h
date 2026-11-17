#pragma once
#include "emu.h"
class edsp_disassembler : public util::disasm_interface {
public:
    edsp_disassembler();
    u32 opcode_alignment() const;
    offs_t disassemble(std::ostream&, offs_t, const data_buffer&, const data_buffer&);
};
