#include "edspdasm.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <iomanip>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    std::ifstream f(argv[1], std::ios::binary);
    if (!f) return 1;
    edsp_disassembler d;
    edsp_disassembler::data_buffer b;
    b.bytes = {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
    for (offs_t pc=0; pc < b.bytes.size()/2;) {
        std::cout << std::hex << std::setfill('0') << std::setw(4) << pc
                  << "  " << std::setw(4) << b.r16(pc) << "  ";
        auto count=d.disassemble(std::cout,pc,b,b)&0xffff;
        std::cout << "\n";
        pc += count ? count : 1;
    }
}
