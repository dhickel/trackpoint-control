import math
import unittest
from trackpoint_control.motion import Motion, Settings


class MotionTests(unittest.TestCase):
    def test_constant_slow_pressure_never_becomes_zero(self):
        # Five minutes at a force just outside the dead zone must retain
        # both its sign and its speed, with no firmware-like 5-second zero.
        m = Motion((100, 200), Settings(smoothing_ms=0))
        windows = []
        for minute in range(5):
            windows.append(sum(m.step((105, 200), .04)[0] for _ in range(1500)))
        self.assertTrue(all(abs(n - 60) <= 1 for n in windows), windows)
        self.assertEqual(m.origin, [100., 200.])

    def test_stationary_noise_emits_nothing(self):
        m = Motion((0, 0))
        for n in range(10000):
            self.assertEqual(m.step((math.sin(n), math.cos(n * .7)), .04), (0, 0))

    def test_small_thermal_drift_is_absorbed_only_inside_idle_region(self):
        m = Motion((0, 0), Settings(smoothing_ms=0))
        for _ in range(15000):
            self.assertEqual(m.step((.5, 0), .04), (0, 0))
        self.assertGreater(m.origin[0], .49)
        original = m.origin[:]
        for _ in range(1000):
            m.step((4, 0), .04)
        self.assertEqual(m.origin, original)

    def test_release_stops_without_reverse_drift(self):
        m = Motion((20, 30))
        for _ in range(500):
            m.step((30, 30), .04)
        released = [m.step((20, 30), .04)[0] for _ in range(100)]
        self.assertTrue(all(x >= 0 for x in released))
        self.assertTrue(all(x == 0 for x in released[3:]))

    def test_pause_does_not_integrate_lost_time(self):
        m = Motion((0, 0))
        self.assertEqual(m.step((30, 0), 10), (0, 0))

    def test_recalibration_and_axis_direction(self):
        m = Motion((0, 0), Settings(smoothing_ms=0))
        self.assertLess(sum(m.step((0, 10), .04)[1] for _ in range(10)), 0)
        m.calibrate((0, 10))
        self.assertEqual(m.step((0, 10), .04), (0, 0))

    def test_invalid_config(self):
        for cfg in (Settings(deadzone=-1), Settings(gain=float('nan')),
                    Settings(idle_radius=5), Settings(curve=0), Settings(invert_x=1)):
            with self.assertRaises(ValueError):
                cfg.validate()


if __name__ == '__main__':
    unittest.main()
