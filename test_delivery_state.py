import unittest
from delivery_state import claim, complete, recover_interrupted

class DeliveryTests(unittest.TestCase):
    def test_claim_and_repeated_jobs(self):
        original = {}
        ledger, ok = claim(original, 'event/part1', 10)
        self.assertTrue(ok)
        self.assertEqual(original, {})
        self.assertFalse(claim(ledger, 'event/part1', 11)[1])
        sent = complete(ledger, 'event/part1', 12, accepted_message_id=7)
        self.assertFalse(claim(sent, 'event/part1', 13)[1])
    def test_ambiguous_timeout_and_crash(self):
        ledger, _ = claim({}, 'event', 10)
        self.assertEqual(complete(ledger, 'event', 11)['event']['state'], 'uncertain')
        recovered = recover_interrupted(ledger)
        self.assertFalse(claim(recovered, 'event', 1000)[1])
    def test_explicit_rate_limit_only(self):
        ledger, _ = claim({}, 'event', 10)
        retry = complete(ledger, 'event', 11, retry_after=4)
        self.assertFalse(claim(retry, 'event', 14)[1])
        self.assertTrue(claim(retry, 'event', 15)[1])
    def test_invalid_transitions(self):
        with self.assertRaises(ValueError):
            complete({}, 'unclaimed', 0, accepted_message_id=5)
        ledger, _ = claim({}, 'event', 10)
        blocked = complete(ledger, 'event', 11, rejected=True)
        self.assertFalse(claim(blocked, 'event', 50)[1])
if __name__ == '__main__':
    unittest.main()
