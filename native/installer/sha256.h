// SHA-256 (FIPS 180-4), for checking downloads against the release manifest.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace inst
{
    class Sha256
    {
    public:
        Sha256();
        void update(const void *data, size_t size);
        std::string hex(); // finishes; lowercase hex digest

    private:
        void block(const uint8_t *p);
        uint32_t m_h[8];
        uint8_t m_buffer[64];
        size_t m_used = 0;
        uint64_t m_bits = 0;
    };
}
