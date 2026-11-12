#pragma once

#include <stddef.h>

// After CAN, accept one fixed-size lowercase-hex challenge without any line
// delimiter. No allocation, unbounded buffering, or partial acknowledgment.
struct SerialInputAbort {
  static constexpr size_t NONCE_LENGTH = 32;
  char nonce[NONCE_LENGTH + 1] = {0};
  size_t length = 0;
  bool collecting = false;

  void begin() {
    length = 0;
    nonce[0] = '\0';
    collecting = true;
  }

  bool consume(char c) {
    if (!collecting) return false;
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
      collecting = false;
      return false;
    }
    nonce[length++] = c;
    nonce[length] = '\0';
    if (length < NONCE_LENGTH) return false;
    collecting = false;
    return true;
  }
};
