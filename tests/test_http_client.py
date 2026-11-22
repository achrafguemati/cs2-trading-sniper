"""Unit tests for read-only HTTP client safety constraints."""

import unittest
from sniper.core.http_client import ReadOnlyHttpClient


class TestHttpClient(unittest.IsolatedAsyncioTestCase):
    async def test_mutation_methods_strictly_prohibited(self):
        client = ReadOnlyHttpClient()
        
        with self.assertRaises(PermissionError):
            await client.post("https://steamcommunity.com/market/buynow")
            
        with self.assertRaises(PermissionError):
            await client.put("https://steamcommunity.com/market/createlisting")
            
        with self.assertRaises(PermissionError):
            await client.delete("https://steamcommunity.com/market/cancellisting")


if __name__ == "__main__":
    unittest.main()
