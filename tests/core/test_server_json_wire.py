"""HTTP wire contract for compact JSON; no store, credentials or external calls."""
import json
import threading
import unittest
import urllib.request
from http.server import HTTPServer

from api.server import _Handler


class JsonWireTests(unittest.TestCase):
    def test_unicode_nested_values_and_byte_length_over_http(self):
        payload = {'executed': True, 'nodes': [{'id': 'concept:synthetic',
                   'label': '합성 ✓', 'props': {'text': 'line\nquote "',
                   'list': [False, None, 0, -0.0, 1.25]}}],
                   'edges': [{'evidence': '합성 근거'}]}

        class Handler(_Handler):
            def do_GET(self):
                self._send(200, payload)

        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/graph') as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(int(response.headers['Content-Length']), len(body))
                    self.assertEqual(response.headers['Content-Type'], 'application/json; charset=utf-8')
                    self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
                    self.assertEqual(json.loads(body), payload)
                    self.assertTrue(body.endswith(b'\n'))
                    self.assertIn('합성'.encode('utf-8'), body)
            finally:
                server.shutdown()
                thread.join()


if __name__ == '__main__':
    unittest.main()
