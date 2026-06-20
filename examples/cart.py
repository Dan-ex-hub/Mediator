"""Tiny shopping-cart helper for the test review."""

CART = {}

def add_item(user_id, sku, qty):
    items = CART.get(user_id, {})
    items[sku] = items.get(sku, 0) + int(qty)
    CART[user_id] = items
    return items

def total(user_id, prices):
    items = CART.get(user_id, {})
    return sum(prices[sku] * q for sku, q in items.items())
