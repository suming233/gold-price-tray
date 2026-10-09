"""Regression cases for v3.11. Run: python -m unittest discover -s tests -v."""
import ctypes
import json
import os
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gold_price_tray as g
import chart


def quote(code='ZS_JCN', price=891.5):
    return dict(code=code, name=g.SOURCES[code], price=price, prev=900.0,
                change=price-900, pct=(price-900)/9, date='2026-10-08', time='21:00:00',
                unit='元/克', cny=True, has_ohlc=False, open=None, high=None, low=None)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = patch.object(g, '_SETTINGS_FILE', os.path.join(self.temp.name, 'settings.json'))
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.log = patch.object(g.GoldTray, '_log')
        self.log.start()
        self.addCleanup(self.log.stop)

    def test_actual_pystray_actions_select_all_sources_and_keep_menu_usable(self):
        tray = g.GoldTray()
        menu = tray.menu()
        submenu = next(i.submenu for i in menu if i.text == '显示品种')
        for code, item in zip(g.SOURCES, submenu):
            item(Mock())  # Real pystray dispatch with its (icon, item) contract.
            self.assertEqual(tray.source, code)
            self.assertIsInstance(tray.source, str)
            self.assertTrue(item.checked)
            self.assertEqual(sum(bool(i.checked) for i in submenu), 1)
            for current in tray.menu():
                str(current.text)  # Former KeyError that destroyed the menu.
            self.assertEqual(g.load_source(), code)

    def test_invalid_source_never_reaches_network(self):
        tray = g.GoldTray()
        with patch.object(g, 'open_quote') as http:
            for code in (None, {}, '浙商银行积存金', 'hf_XAU\n'):
                self.assertFalse(tray.set_source(code))
                self.assertIn('error', g.fetch_quote(code))
            http.assert_not_called()

    def test_settings_preserve_hover_delay_and_other_fields(self):
        with open(g._SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'hover_delay_ms': 600, 'other': 42}, f)
        g.save_source('hf_XAU')
        self.assertEqual(g.load_hover_delay_ms(), 600)
        with open(g._SETTINGS_FILE, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['other'], 42)

    def test_corrupt_and_invalid_settings_fall_back(self):
        for text in ('{', '[]', '{"source": []}', '{"source": "bad"}'):
            with open(g._SETTINGS_FILE, 'w', encoding='utf-8') as f:
                f.write(text)
            self.assertEqual(g.load_source(), g.DEFAULT_SOURCE)

    def test_late_old_source_response_is_discarded(self):
        tray = g.GoldTray()
        entered, release = threading.Event(), threading.Event()
        def delayed(code):
            entered.set()
            self.assertTrue(release.wait(3))
            return quote(code)
        with patch.object(g, 'fetch_quote', side_effect=delayed):
            worker = threading.Thread(target=tray._refresh_worker)
            worker.start()
            self.assertTrue(entered.wait(3))
            tray.set_source('hf_XAU')
            release.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertIsNone(tray.quote)
        with patch.object(g, 'fetch_quote', return_value=quote('hf_XAU', 888)):
            tray._refresh_worker()
        self.assertEqual(tray.quote['code'], 'hf_XAU')

    def test_refresh_clicks_coalesce_without_spawning_threads(self):
        tray = g.GoldTray()
        with patch.object(threading, 'Thread') as thread:
            for _ in range(100):
                tray.refresh_now()
            thread.assert_not_called()
        self.assertTrue(tray._refresh_event.is_set())

    def test_fetch_failure_preserves_price_marks_stale_and_recovers(self):
        tray = g.GoldTray()
        tray.icon = Mock()
        tray._apply_quote(quote())
        tray._apply_quote({'error': 'offline'})
        self.assertEqual(tray.quote['price'], 891.5)
        self.assertTrue(tray.quote['stale'])
        self.assertIn('上次报价', tray._last_title)
        tray._apply_quote(quote(price=892))
        self.assertNotIn('stale', tray.quote)
        self.assertIsNone(tray.last_error)
        self.assertEqual(tray._err_streak, 0)

    def test_switch_clears_old_price_and_first_failure_has_named_tooltip(self):
        tray = g.GoldTray()
        tray.icon = Mock()
        tray._apply_quote(quote())
        tray.set_source('hf_XAU')
        self.assertIsNone(tray.quote)
        tray._apply_quote({'error': 'offline'})
        self.assertIn(g.SOURCES['hf_XAU'], tray._last_title)

    def test_tooltip_is_bounded(self):
        tray = g.GoldTray()
        tray.icon = Mock()
        tray._last_title = '错' * 500
        tray._sync_tray_title()
        self.assertEqual(len(tray.icon.title), 127)

    def test_domestic_quotes_have_yuan_precision(self):
        raw = 'var hq_str_gds_AU9999="891.50,0,0,0,895,888,21:00:00,900,890,0,0,0,2026-10-08,沪金99";'
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = raw.encode('gbk')
        with patch.object(g, 'open_quote', return_value=response):
            q = g.fetch_quote('gds_AU9999')
        self.assertTrue(q['cny'])
        self.assertEqual(q['price'], 891.5)

    def test_bad_numeric_quotes_and_missing_fx_are_errors(self):
        for price, rate in [('nan', 7), ('0', 7), ('4100', None)]:
            raw = f'var hq_str_hf_XAU="{price},0,0,0,4200,4000,21:00:00,4100,4100,0,0,0,2026-10-08,伦敦金";'
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw.encode('gbk')
            with patch.object(g, 'open_quote', return_value=response), patch.object(g, '_get_cny_rate', return_value=rate):
                self.assertIn('error', g.fetch_quote('hf_XAU'))

    def test_chart_cannot_reuse_other_market_or_stale_quote(self):
        with patch.object(chart, 'fetch_spot_quote', return_value=None):
            for q in (quote(), quote('hf_GC'), dict(quote('hf_XAU'), stale=True)):
                self.assertIsNone(chart.fetch_kline_quote(q))
            london = quote('hf_XAU')
            self.assertIs(chart.fetch_kline_quote(london), london)

    def test_chart_currency_failure_does_not_return_dollars(self):
        for function, data in [(chart.fetch_daily_kline, 't=([{"date":"2026-10-08","close":"4100"}]);'),
                               (chart.fetch_minute_kline, 't=({"minLine_1d":[["21:00","4100","2026-10-08 21:00:00"]]});')]:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = data.encode()
            with patch.object(chart, 'open_quote', return_value=response), patch.object(chart, '_get_cny_rate', return_value=None):
                self.assertEqual(function(), [])

    def test_chart_late_result_cannot_overwrite_new_mode(self):
        window = chart.ChartWindow.__new__(chart.ChartWindow)
        window._closed, window._request_id = False, 2
        window.root, window._redraw = Mock(), Mock()
        import queue
        window.q = queue.Queue()
        fresh = dict(title='month', data=[('10-08', 890)], latest=(890, 0, 0, '伦敦金'),
                     prev_line=None, data_time='2026-10-08', unit='元/克')
        window.q.put((2, fresh))
        window.q.put((1, {'error': 'old request failed'}))
        window._poll()
        self.assertEqual(window.title_line, 'month')
        self.assertEqual(window.data, fresh['data'])
        window._redraw.assert_called_once()

    def test_chart_previous_session_is_labeled_and_not_mixed_with_today(self):
        window = chart.ChartWindow.__new__(chart.ChartWindow)
        window.get_quote = lambda: quote('hf_XAU')
        rows = [('2026-10-07 21:00:00', 889), ('2026-10-07 21:05:00', 890)]
        with patch.object(chart, 'fetch_minute_kline', return_value=rows) as fetch, patch.object(chart, 'fetch_kline_quote', return_value=quote('hf_XAU')), patch.object(chart.time, 'strftime', return_value='2026-10-08'):
            result = window._fetch_day()
        self.assertIn('最近交易日 2026-10-07', result['title'])
        self.assertEqual(result['latest'][0], 890)
        self.assertIsNone(result['prev_line'])
        fetch.assert_called_once()

    def test_hover_delay_hide_grace_and_fullscreen_reset(self):
        card = g.HoverCard(lambda: quote(), on_zone=Mock())
        card.root = Mock()
        card.delay_ms = 1000
        def show(*args):
            card.visible = True
        card._show = Mock(side_effect=show)
        with patch.object(g, '_get_tray_rect', return_value=(100, 900, 200, 950)), patch.object(g, '_cursor_pos', return_value=(150, 925)), patch.object(g, '_is_fullscreen_foreground', return_value=False), patch.object(card, '_hit_test', return_value=True), patch.object(g.time, 'monotonic') as now:
            now.return_value = 100.0
            card._poll()
            self.assertFalse(card.visible)
            now.return_value = 100.9
            card._poll()
            self.assertFalse(card.visible)
            now.return_value = 101.1
            card._poll()
            self.assertTrue(card.visible)
            with patch.object(card, '_hit_test', return_value=False):
                now.return_value = 101.2
                card._poll()
                self.assertTrue(card.visible)
                now.return_value = 101.6
                card._poll()
                self.assertFalse(card.visible)
            now.return_value = 102.0
            card._poll()
            self.assertTrue(card.zone)
            with patch.object(g, '_is_fullscreen_foreground', return_value=True):
                card._poll()
            self.assertFalse(card.zone)
            self.assertFalse(card.visible)

    def test_bridge_covers_only_gap_and_no_notification_area_fallback(self):
        card = g.HoverCard(lambda: quote())
        card.visible, card.card_rect = True, (200, 500, 480, 850)
        with patch.object(card, '_icon_rect_cached', return_value=(440, 860, 480, 900)):
            self.assertTrue(card._hit_test((460, 855), 1))
            self.assertFalse(card._hit_test((220, 880), 1))
        card.visible = False
        with patch.object(card, '_icon_rect_cached', return_value=None):
            self.assertFalse(card._hit_test((460, 880), 1))

    def test_lost_icon_discards_previous_hot_zone(self):
        card = g.HoverCard(lambda: quote())
        card._icon_rect = (1, 2, 3, 4)
        with patch.object(g, '_tray_icon_hwnd', return_value=None):
            self.assertIsNone(card._icon_rect_cached(10))

    def test_icon_identifier_matches_installed_pystray_backend(self):
        from pystray._util.win32 import NOTIFYICONDATAW
        self.assertEqual(NOTIFYICONDATAW(hID=123456789).uID, 0)

    def test_failed_autostart_does_not_report_success(self):
        tray = g.GoldTray()
        tray._toast = Mock()
        with patch.object(g, 'set_autostart', return_value=False):
            tray.toggle_autostart(Mock(), None)
        self.assertIn('失败', tray._toast.call_args.args[0])

    def test_install_copy_failure_preserves_existing_binary(self):
        installed = os.path.join(self.temp.name, 'GoldPriceTray.exe')
        with open(installed, 'wb') as f:
            f.write(b'old working binary')
        with patch.object(g, 'INSTALL_DIR', self.temp.name), patch.object(g, 'INSTALLED_EXE', installed), patch.object(g.shutil, 'copy2', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                g.do_install(False, False, False)
        with open(installed, 'rb') as f:
            self.assertEqual(f.read(), b'old working binary')

    def test_uninstall_rejects_unexpected_path_before_mutations(self):
        with patch.object(g, 'INSTALL_DIR', self.temp.name), patch.object(g, 'set_autostart') as registry:
            with self.assertRaises(ValueError):
                g.do_uninstall()
            registry.assert_not_called()

    def test_uninstall_script_quotes_unicode_and_limits_process_scope(self):
        script = g._uninstall_script("C:\\用户\\O'Brien\\GoldPriceTray", "C:\\用户\\O'Brien\\GoldPriceTray\\GoldPriceTray.exe")
        self.assertIn("O''Brien", script)
        self.assertIn('ExecutablePath -eq $targetExe', script)
        self.assertIn('Resolve-Path -LiteralPath', script)
        self.assertNotIn('cmd.exe', script)

    def test_log_rotation_has_hard_byte_bound_even_with_huge_lines(self):
        logfile = os.path.join(self.temp.name, 'test.log')
        with open(logfile, 'wb') as f:
            f.write(('错误' * 150000).encode('utf-8'))
        with patch.object(g.GoldTray, '_log_path', return_value=logfile):
            g.GoldTray._write_log('错' * 300000)
        self.assertLessEqual(os.path.getsize(logfile), g.LOG_MAX_BYTES)
        with open(logfile, encoding='utf-8') as f:
            self.assertIn('错', f.read())


if __name__ == '__main__':
    unittest.main()
