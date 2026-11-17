"""Force-to-velocity conversion, independent of firmware recalibration.

Only sensor measurements enter this filter. Firmware movement deltas and
subsequent firmware origin updates are deliberately not used.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Settings:
    deadzone: float = 4.0
    gain: float = 1.0
    curve: float = 1.2
    smoothing_ms: float = 5.0
    idle_radius: float = 2.0
    idle_seconds: float = 5.0
    drift_tau_seconds: float = 30.0
    max_speed: float = 1800.0
    invert_x: bool = False
    invert_y: bool = True

    def validate(self):
        limits = {
            'deadzone': (0, 100), 'gain': (0.01, 500), 'curve': (1, 3),
            'smoothing_ms': (0, 1000), 'idle_radius': (0, 100),
            'idle_seconds': (1, 3600), 'drift_tau_seconds': (1, 86400),
            'max_speed': (1, 10000),
        }
        for name, (lo, hi) in limits.items():
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f'{name} must be between {lo} and {hi}')
        if self.idle_radius > self.deadzone:
            raise ValueError('idle_radius must not exceed deadzone')
        for name in ('invert_x', 'invert_y'):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f'{name} must be a boolean')


def signed16(value):
    return value - 65536 if value & 32768 else value


class Motion:
    def __init__(self, origin, settings=Settings()):
        settings.validate()
        self.settings = settings
        self.origin = list(map(float, origin))
        self.filtered = [0., 0.]
        self.remainder = [0., 0.]
        self.idle_elapsed = 0.

    def calibrate(self, origin):
        self.origin = list(map(float, origin))
        self.filtered = [0., 0.]
        self.remainder = [0., 0.]
        self.idle_elapsed = 0.

    def step(self, sample, dt, buttons=0):
        if not 0 < dt <= .25:
            # Do not integrate a pause/suspend into a large cursor movement.
            self.remainder = [0., 0.]
            self.filtered = [0., 0.]
            self.idle_elapsed = 0.
            return 0, 0
        cfg = self.settings
        force = [float(sample[i]) - self.origin[i] for i in range(2)]
        alpha = 1 if cfg.smoothing_ms == 0 else -math.expm1(-dt / (cfg.smoothing_ms / 1000))
        for i in range(2):
            self.filtered[i] += alpha * (force[i] - self.filtered[i])
        distance = math.hypot(*self.filtered)
        # Slow thermal tracking is confined to the existing stationary zone.
        # A steady force outside it can NEVER become a new zero, regardless
        # of how long the user holds it. Zero idle_radius disables tracking.
        if cfg.idle_radius > 0 and distance <= cfg.idle_radius and not buttons:
            self.idle_elapsed += dt
            if self.idle_elapsed >= cfg.idle_seconds:
                beta = -math.expm1(-dt / cfg.drift_tau_seconds)
                for i in range(2):
                    change = beta * self.filtered[i]
                    self.origin[i] += change
                    self.filtered[i] -= change
        else:
            self.idle_elapsed = 0.
        if distance <= cfg.deadzone:
            self.remainder = [0., 0.]
            return 0, 0
        speed = min(cfg.max_speed, cfg.gain * (distance - cfg.deadzone) ** cfg.curve)
        result = []
        for i, invert in enumerate((cfg.invert_x, cfg.invert_y)):
            d = self.filtered[i] / distance * speed * dt * (-1 if invert else 1)
            self.remainder[i] += d
            integer = math.trunc(self.remainder[i])
            self.remainder[i] -= integer
            result.append(integer)
        return tuple(result)
