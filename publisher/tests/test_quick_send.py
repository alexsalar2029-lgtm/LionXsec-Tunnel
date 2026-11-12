import unittest
import test_publisher
from unittest.mock import patch

class QuickSendTests(unittest.TestCase):
    setUp=test_publisher.PublisherTests.setUp
    tearDown=test_publisher.PublisherTests.tearDown
    source=test_publisher.PublisherTests.source
    scan=test_publisher.PublisherTests.scan
    def test_quick_send_while_paused(self):
        self.source();self.scan()
        with patch.object(self.engine,'build_cover',return_value=(self.source('cover.jpg'),'local')):
            self.assertEqual(self.engine.send_now(1)['status'],'sent')
        self.assertFalse(self.store.settings()['enabled'])
        with self.assertRaises(ValueError): self.engine.send_now(1)

    def test_quick_send_rejects_unreviewed(self):
        self.source();self.scan()
        with self.store.db() as db: db.execute("UPDATE jobs SET error='analysis_required' WHERE id=1")
        with self.assertRaises(ValueError): self.engine.send_now(1)
