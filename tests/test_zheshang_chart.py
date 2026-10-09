import json
import queue
import unittest
from unittest.mock import Mock, patch

import chart
import gold_price_tray as g


class ZheshangChartTests(unittest.TestCase):
    def response(self, data):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(data).encode()
        return response

    def window(self, quote=None):
        window = chart.ChartWindow.__new__(chart.ChartWindow)
        window.get_quote = lambda: quote
        return window

    def test_official_day_request_uses_zheshang_sku_and_yuan_prices(self):
        entries = [dict(goldPriceTime='2026-10-09 10:01:00', goldPrice='903.2'),
                   dict(goldPriceTime='2026-10-09 10:00:00', goldPrice='902.1'),
                   dict(goldPriceTime='2026-10-09 10:02:00', goldPrice='NaN'),
                   dict(goldPriceTime='invalid', goldPrice='902')]
        payload = dict(success=True, resultData=dict(code='00000000', data=dict(dataList=entries)))
        with patch.object(chart, 'open_quote', return_value=self.response(payload)) as request:
            rows = chart.fetch_zheshang_history('day')
        self.assertEqual(rows, [('2026-10-09 10:00:00', 902.1), ('2026-10-09 10:01:00', 903.2)])
        from urllib.parse import parse_qs
        req = request.call_args.args[0]
        data = json.loads(parse_qs(req.data.decode())['reqData'][0])
        self.assertEqual(data['productSku'], '1961543816')
        self.assertEqual(data['priceType'], 'buy')

    def test_history_failure_is_not_relabelled_london_data(self):
        with patch.object(chart, 'open_quote', return_value=self.response(dict(success=False))), \
                patch.object(chart, 'fetch_minute_kline') as london:
            with self.assertRaises(ValueError):
                self.window()._fetch_zheshang('day')
            london.assert_not_called()

    def test_day_uses_zheshang_quote_even_when_tray_selects_london(self):
        zs = dict(code='ZS_JCN', price=904.0, prev=889.0, date='2026-10-09', time='10:02:00')
        window = self.window(dict(code='hf_XAU', price=800, prev=799))
        with patch.object(chart, 'fetch_zheshang_history', return_value=[('2026-10-09 10:01:00', 903.0)]), \
                patch.object(g, 'fetch_zheshang_quote', return_value=zs) as fetch:
            result = window._fetch_zheshang('day')
        fetch.assert_called_once_with(include_reference=False)
        self.assertEqual(result['latest'][:2], (904.0, 15.0))
        self.assertEqual(result['data'][-1], ('10:02', 904.0))
        self.assertEqual(result['prev_line'][0], 889.0)
        self.assertIn('浙商', result['title'])

    def test_old_spot_quote_cannot_replace_newer_history_or_other_date_baseline(self):
        zs = dict(code='ZS_JCN', price=800, prev=790, date='2026-10-08', time='10:00:00')
        with patch.object(chart, 'fetch_zheshang_history', return_value=[('2026-10-09 10:01:00', 903.0)]):
            result = self.window(zs)._fetch_zheshang('day')
        self.assertEqual(result['latest'][0], 903.0)
        self.assertIsNone(result['prev_line'])
        self.assertIsNone(result['latest'][1])

    def test_week_and_month_keep_bank_history_and_interval_change(self):
        rows = [(f'2026-10-{i:02d}', 880.0 + i) for i in range(1, 10)]
        with patch.object(chart, 'fetch_zheshang_history', return_value=rows):
            week = self.window()._fetch_zheshang('week')
            month = self.window()._fetch_zheshang('month')
        self.assertEqual(len(week['data']), 5)
        self.assertEqual(len(month['data']), 9)
        self.assertEqual(week['latest'][1], 4.0)
        self.assertEqual(month['latest'][1], 8.0)
        self.assertIsNone(month['prev_line'])

    def test_worker_uses_captured_market_after_ui_switch(self):
        window = self.window()
        window.market = 'hf_XAU'
        window.q = queue.Queue()
        with patch.object(window, '_fetch_zheshang', return_value={'title': '浙商'}) as zs, \
                patch.object(window, '_fetch_day') as london:
            window._fetch_worker(7, 'day', 'ZS_JCN')
        zs.assert_called_once_with('day')
        london.assert_not_called()
        self.assertEqual(window.q.get(), (7, {'title': '浙商'}))


if __name__ == '__main__':
    unittest.main()
