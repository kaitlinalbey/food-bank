import unittest


class AuthTests(unittest.TestCase):
    def test_login_success_returns_token(self): self.skipTest('not written yet')
    def test_login_wrong_password_is_401(self): self.skipTest('not written yet')
    def test_protected_route_without_token_is_401(self): self.skipTest('not written yet')


class InventoryTests(unittest.TestCase):
    def test_view_other_bank_inventory(self): self.skipTest('not written yet')
    def test_update_own_inventory(self): self.skipTest('not written yet')
    def test_cannot_set_negative_quantity(self): self.skipTest('not written yet')
    def test_shortage_and_surplus_flags(self): self.skipTest('not written yet')


class ExchangeTests(unittest.TestCase):
    def test_create_exchange(self): self.skipTest('not written yet')
    def test_same_request_id_does_not_duplicate(self): self.skipTest('not written yet')
    def test_cannot_request_from_own_bank(self): self.skipTest('not written yet')
    def test_only_supplier_can_accept_or_decline(self): self.skipTest('not written yet')
    def test_complete_moves_stock_between_banks(self): self.skipTest('not written yet')
    def test_complete_fails_if_supplier_lacks_stock(self): self.skipTest('not written yet')
    def test_state_change_creates_notification(self): self.skipTest('not written yet')


if __name__ == '__main__':
    unittest.main()
