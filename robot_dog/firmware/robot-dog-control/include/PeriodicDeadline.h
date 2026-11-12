#pragma once

#include <stdint.h>

// Preserve the clock phase after a late poll. Skip missed periods instead of
// emitting catch-up bursts. Deadlines and periods must be within 2^31 ms.
inline bool consumePeriodicDeadline(
    uint32_t now, uint32_t period, uint32_t &next, uint32_t &missed) {
  if (static_cast<int32_t>(now - next) < 0) return false;
  const uint32_t skipped = (now - next) / period;
  next += (skipped + 1) * period;
  missed += skipped;
  return true;
}
