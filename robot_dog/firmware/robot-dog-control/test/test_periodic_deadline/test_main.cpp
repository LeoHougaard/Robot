#include <assert.h>
#include "PeriodicDeadline.h"

int main() {
  uint32_t next = 20, missed = 0;
  assert(!consumePeriodicDeadline(19, 20, next, missed));
  assert(consumePeriodicDeadline(23, 20, next, missed));
  assert(next == 40 && missed == 0);
  assert(!consumePeriodicDeadline(23, 20, next, missed));
  assert(consumePeriodicDeadline(40, 20, next, missed));
  assert(next == 60);
  assert(consumePeriodicDeadline(105, 20, next, missed));
  assert(next == 120 && missed == 2);
  assert(!consumePeriodicDeadline(105, 20, next, missed));

  next = UINT32_MAX - 9;
  missed = 0;
  assert(!consumePeriodicDeadline(UINT32_MAX - 10, 20, next, missed));
  assert(consumePeriodicDeadline(UINT32_MAX - 8, 20, next, missed));
  assert(next == 10 && missed == 0);
  assert(!consumePeriodicDeadline(9, 20, next, missed));
  assert(consumePeriodicDeadline(10, 20, next, missed));
  assert(next == 30 && missed == 0);

  next = 20;
  unsigned count = 0;
  for (uint32_t now = 0; now < 10000; ++now) {
    // Repeated 3 ms delays must not accumulate into a slower clock.
    if (now % 20 < 3) continue;
    count += consumePeriodicDeadline(now, 20, next, missed);
  }
  assert(count == 499 && missed == 0);
}
