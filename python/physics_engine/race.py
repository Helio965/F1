"""Race control: ordered sectors, lap validity, timing and standings.

A lap counts only when all sectors are crossed in order and the progress
along the centre line is plausible (no teleport or shortcut). Leaving the
track (car centre beyond the asphalt edge by more than half a car width, i.e.
all four wheels outside the white line) invalidates the lap time for records
but still counts the lap for race distance, like a track-limits violation.
"""

SECTORS = 20


class RaceTracker:
    __slots__ = (
        "car_id",
        "credited",
        "since_line",
        "completed",
        "started",
        "next_sector",
        "valid",
        "clean",
        "finish_time",
        "lap_time",
        "best_lap",
        "last_lap",
        "finished",
        "progress",
        "laps_target",
        "lap_events",
    )

    def __init__(self, car_id, start_progress, laps_target):
        self.car_id = car_id
        self.credited = start_progress
        self.since_line = 0.0
        self.completed = 0
        self.started = False
        self.next_sector = 1
        self.valid = True
        self.clean = True
        self.finish_time = float("inf")
        self.lap_time = 0.0
        self.best_lap = None
        self.last_lap = None
        self.finished = False
        self.progress = 0.0
        self.laps_target = laps_target
        self.lap_events = []

    @property
    def lap(self):
        return self.completed + 1

    def update(self, old_s, new_s, length, speed, lateral, half_width, car_width, dt, time):
        delta = new_s - old_s
        if delta < -0.5 * length:
            delta += length
        elif delta > 0.5 * length:
            delta -= length
        self.lap_time += dt
        self.progress = new_s
        plausible = abs(delta) < max(5.0, (abs(speed) + 10.0) * dt * 3.0)
        if not plausible:
            self.valid = False
        if abs(lateral) > half_width + 0.5 * car_width:
            self.clean = False
        if plausible:
            self.credited += delta
            if self.started:
                self.since_line += delta
            old_sector = int(old_s / length * SECTORS)
            new_sector = int(new_s / length * SECTORS)
            if delta > 0.0 and new_sector != old_sector:
                if new_sector == self.next_sector:
                    self.next_sector += 1
                elif new_sector != 0 and new_sector > self.next_sector:
                    self.valid = False
            crossed = delta > 0.0 and old_s > 0.9 * length and new_s < 0.1 * length
            if crossed:
                if self.started and self.valid and self.next_sector == SECTORS and self.since_line > 0.9 * length:
                    self.completed += 1
                    self.last_lap = self.lap_time
                    if self.clean and (self.best_lap is None or self.lap_time < self.best_lap):
                        self.best_lap = self.lap_time
                    self.lap_events.append((self.completed, self.lap_time, self.clean))
                    if self.completed >= self.laps_target:
                        self.finished = True
                        self.finish_time = time
                self.started = True
                self.since_line = new_s
                self.next_sector = 1
                self.valid = True
                self.clean = True
                self.lap_time = 0.0
                return True
        return False


def standings(trackers):
    def key(t):
        if t.finished:
            return (0, t.finish_time, t.car_id)
        return (1, -t.credited, t.car_id)

    return sorted(trackers, key=key)
