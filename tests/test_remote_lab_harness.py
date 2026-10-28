"""Exercise the lab runner's port check with real listening and TIME_WAIT sockets."""
import ast
from pathlib import Path
import socket
import unittest


class LabPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parents[1] / 'scripts/remote-lab-smoke.sh').read_text()
        python = source.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        function = next(n for n in ast.parse(python).body if isinstance(n, ast.FunctionDef) and n.name == 'assert_port_free')
        namespace = {'socket': socket}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<lab-port-check>', 'exec'), namespace)
        cls.check_port = staticmethod(namespace['assert_port_free'])

    def test_live_listener_is_rejected_and_time_wait_is_accepted(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            listener.listen(1)
            with self.assertRaises(OSError):
                self.check_port(port)
            with socket.create_connection(('127.0.0.1', port)) as client:
                accepted, _ = listener.accept()
                accepted.close()  # the server side enters TIME_WAIT after the peer closes
                self.assertEqual(client.recv(1), b'')
        self.check_port(port)
