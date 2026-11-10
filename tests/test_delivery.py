import copy
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test')
os.environ.setdefault('TELEGRAM_CHAT_ID', 'test')
os.environ.setdefault('GITHUB_TOKEN', 'test')
os.environ.setdefault('GITHUB_REPOSITORY', 'test/test')
os.environ.setdefault('TWELVE_DATA_API_KEY', 'test')
import scanner
import telegram_alerts as alerts


class DeliveryTests(unittest.TestCase):
    def test_progress_saved_before_later_delivery_fails(self):
        trade = dict(symbol='XAUUSD', strategy='test', side='LONG', entry=100,
                     sl=90, tp1=101, tp2=102, tp3=103, hit=[],
                     opened_at='2026-11-10T00:00:00+00:00')
        state = dict(sent=['id'], active={'id': trade})
        saved = []
        bar = scanner.Candle('2026-11-10 00:05:00', 100, 104, 99, 103)
        with patch.object(alerts, 'get_candles', return_value=[bar]), \
             patch.object(alerts, 'send', side_effect=[None, RuntimeError('offline')]):
            with self.assertRaises(RuntimeError):
                alerts.track_active(state, lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual(saved[0]['active']['id']['hit'], ['tp1'])

    def test_final_target_persisted_as_closed(self):
        state = dict(sent=['id'], active={'id': dict(symbol='XAUUSD', strategy='test',
            side='LONG', sl=90, tp1=101, tp2=102, tp3=103, hit=['tp1', 'tp2'],
            opened_at='2026-11-10T00:00:00+00:00')})
        saved = []
        bar = scanner.Candle('2026-11-10 00:05:00', 100, 104, 99, 103)
        with patch.object(alerts, 'get_candles', return_value=[bar]), patch.object(alerts, 'send'):
            alerts.track_active(state, lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual(saved[-1]['active'], {})

    def test_market_failure_does_not_report_success(self):
        with patch.object(scanner, 'atr_candle_breakout_xau', side_effect=RuntimeError('429'), __name__='atr_candle_breakout_xau'), \
             patch.object(scanner, 'donchian_scalper_xau', return_value=None):
            with self.assertRaises(RuntimeError):
                scanner.scan_all()

    def test_price_request_shared_by_both_strategies(self):
        alerts._PRICE_CACHE.clear()
        response = Mock(ok=True)
        response.json.return_value = {'price': '4100'}
        with patch.object(alerts.requests, 'get', return_value=response) as request:
            self.assertEqual(alerts.current_price('XAUUSD'), 4100)
            self.assertEqual(alerts.current_price('XAUUSD'), 4100)
            self.assertEqual(request.call_count, 1)

    def test_candle_request_shared_with_tracking(self):
        scanner._CANDLE_CACHE.clear()
        bars = [scanner.Candle('2026-11-10 00:00:00', 100, 101, 99, 100)]
        scanner._CANDLE_CACHE[('XAU/USD', '5min', 100)] = bars
        with patch.object(scanner.requests, 'get') as request:
            self.assertEqual(scanner.get_candles('XAU/USD', '5min', 100), bars)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
