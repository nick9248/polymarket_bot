"""
diagnose_wallet.py
Run this on the VPS to diagnose the wallet type and balance situation.
Usage: python diagnose_wallet.py
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

POLY_RPC = "https://polygon-rpc.com"
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

# USDC contract on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

pk = os.getenv("poly_private_key", "").strip(" '\"")
poly_address = os.getenv("poly_address", "").strip(" '\"")


def rpc_call(method, params):
    resp = requests.post(POLY_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=15)
    return resp.json().get("result")


def check_contract_code(address):
    """Returns True if address has contract code (is a smart contract), False if EOA."""
    code = rpc_call("eth_getCode", [address, "latest"])
    return code and code != "0x"


def check_usdc_balance(address):
    """Check actual USDC balance on Polygon via ERC-20 balanceOf."""
    # balanceOf(address) selector = 0x70a08231
    padded = address.lower().replace("0x", "").zfill(64)
    data = "0x70a08231" + padded
    result = rpc_call("eth_call", [{"to": USDC_ADDRESS, "data": data}, "latest"])
    if result and result != "0x":
        return int(result, 16) / 1_000_000
    return 0.0


def check_clob_balance(pk, sig_type):
    """Check CLOB balance using given signature type."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        client = ClobClient(
            host=CLOB_HOST,
            key=pk,
            chain_id=137,
            signature_type=sig_type,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        derived_addr = client.get_address()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params)
        raw = resp.get("balance", "0")
        balance = float(raw) / 1_000_000
        return derived_addr, balance
    except Exception as e:
        return None, f"ERROR: {e}"


def check_gamma_profile(address):
    """Query gamma API for profile info."""
    try:
        resp = requests.get(f"{GAMMA_HOST}/profiles", params={"addresses": address}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"ERROR: {e}"


def check_clob_trades(address):
    """Check if any trades exist for this address on the CLOB."""
    try:
        resp = requests.get(f"{CLOB_HOST}/trades", params={"maker_address": address, "limit": 5}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data
        return f"HTTP {resp.status_code}"
    except Exception as e:
        return f"ERROR: {e}"


print("=" * 60)
print("POLYMARKET WALLET DIAGNOSTICS")
print("=" * 60)

print(f"\n[1] .env values:")
print(f"    poly_address = {poly_address}")
print(f"    poly_private_key present = {bool(pk)}")

print(f"\n[2] On-chain check for {poly_address}:")
is_contract = check_contract_code(poly_address)
print(f"    Is smart contract: {is_contract}")
print(f"    Is EOA: {not is_contract}")

usdc_balance = check_usdc_balance(poly_address)
print(f"    Direct USDC balance on Polygon: ${usdc_balance:.6f}")

print(f"\n[3] CLOB balance with signature_type=0 (EOA):")
addr0, bal0 = check_clob_balance(pk, 0)
print(f"    Derived address: {addr0}")
print(f"    CLOB USDC balance: {bal0}")

print(f"\n[4] CLOB balance with signature_type=1 (POLY_PROXY):")
addr1, bal1 = check_clob_balance(pk, 1)
print(f"    Derived address: {addr1}")
print(f"    CLOB USDC balance: {bal1}")

print(f"\n[5] Gamma API profile for {poly_address}:")
profile = check_gamma_profile(poly_address)
print(f"    Result: {json.dumps(profile, indent=2) if isinstance(profile, (dict, list)) else profile}")

print(f"\n[6] CLOB trades for maker={poly_address}:")
trades = check_clob_trades(poly_address)
print(f"    Result: {trades}")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
