import base64
import based58
import httpx
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.transaction import Transaction
from spl.token.instructions import get_associated_token_address, create_associated_token_account

from usdc_swaps import route_map

SOLANA_CLIENT = AsyncClient('https://api.mainnet-beta.solana.com')
USDC_BASE = 1000000
GENERATED_ROUTE_MAP = route_map

# Default trade amount
trade_amount = 5000000

# Default wallet
WALLET = Keypair.from_secret_key(based58.b58decode('secret_key'.encode("ascii")))

# Threshold for low balance notification
LOW_BALANCE_THRESHOLD = 1000000

def get_mint(index, indexedRouteMap):
    return indexedRouteMap['mintKeys'][int(index)]

def get_route_map():
    return GENERATED_ROUTE_MAP

async def get_wallet_balance():
    try:
        balance = await SOLANA_CLIENT.get_balance(WALLET.public_key)
        return balance
    except Exception as e:
        print("Error occurred while getting wallet balance: ", str(e))
        return None

async def get_coin_quote(INPUT_MINT, TOKEN_MINT, amount):
    # Get PAIR QUOTE
    url = f'https://quote-api.jup.ag/v1/quote?inputMint={INPUT_MINT}&outputMint={TOKEN_MINT}&amount={amount}&slippage=0.5'
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15.0)
        return r.json()

async def get_coin_swap_quote(route):
    # Get PAIR SWAP QUOTE
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url='https://quote-api.jup.ag/v1/swap',
            json={
                'route': route,
                'userPublicKey': str(WALLET.public_key),
                'wrapUnwrapSOL': False
            },
            timeout=15.0
        )
        return r.json()

async def execute_transaction(transactions):
    # Execute transactions
    opts = TxOpts(skip_preflight=True, max_retries=11)
    for tx_name, raw_transaction in transactions.items():
        if raw_transaction:
            try:
                transaction = Transaction.deserialize(base64.b64decode(raw_transaction))
                await SOLANA_CLIENT.send_transaction(transaction, WALLET, opts=opts)
            except Exception as e:
                print("Error occurred at execution of transaction: ", str(e))
                return str(e)

async def serialized_swap_transaction(usdc_to_token_route, token_to_usdc_route):
    if usdc_to_token_route:
        try:
            usdc_to_token_transaction = await get_coin_swap_quote(usdc_to_token_route)
            await execute_transaction(usdc_to_token_transaction)
        except Exception as e:
            print("Error occurred at execution usdc to token: ", str(e))
            return str(e)

        if token_to_usdc_route:
            try:
                token_to_usdc_transaction = await get_coin_swap_quote(token_to_usdc_route)
                await execute_transaction(token_to_usdc_transaction)
            except Exception as e:
                print("Error occurred at execution token to usdc: ", str(e))
                return str(e)

async def _create_associated_token_account(token):
    # Create Associated token account for token to swap if not available
    token_associated_account = get_associated_token_address(
        WALLET.public_key,
        PublicKey(token)
    )
    opts = TxOpts(skip_preflight=True, max_retries=11)
    ata = await SOLANA_CLIENT.get_account_info(PublicKey(token_associated_account))
    if not ata.get('result').get('value'):
        try:
            instruction = create_associated_token_account(
                WALLET.public_key,
                WALLET.public_key,
                PublicKey(token)
            )
            txn = Transaction().add(instruction)
            txn.recent_blockhash = await SOLANA_CLIENT.get_recent_blockhash()
            await SOLANA_CLIENT.send_transaction(txn, WALLET, opts=opts)
        except Exception as e:
            print("Error occurred while creating ATA: ", str(e))
            return e
    else:
        print("Associated token account exists: ", ata)

async def swap(update: Update, context: CallbackContext):
    global trade_amount
    while True:
        wallet_balance = await get_wallet_balance()

        if wallet_balance is not None:
            if wallet_balance < LOW_BALANCE_THRESHOLD:
                update.message.reply_text("Warning: Your wallet balance is low!")

            for token in GENERATED_ROUTE_MAP[:150]:
                usdc_to_token = await get_coin_quote(
                    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
                    token,
                    trade_amount
                )
                if usdc_to_token.get('data'):
                    token_to_usdc = await get_coin_quote(
                        token,
                        'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
                        usdc_to_token.get('data')[0].get('otherAmountThreshold')
                    )
                    if token_to_usdc.get('data'):
                        if token_to_usdc.get('data')[0].get('otherAmountThreshold') > trade_amount:
                            await _create_associated_token_account(token)
                            await serialized_swap_transaction(
                                usdc_to_token.get('data')[0],
                                token_to_usdc.get('data')[0]
                            )
                            profit = token_to_usdc.get('data')[0].get('otherAmountThreshold') - trade_amount
                            update.message.reply_text(f"Approx Profit made: {profit / USDC_BASE}")

async def show_last_trade_stats(update: Update, context: CallbackContext):
    # Implement logic to show the last trade statistics
    update.message.reply_text("Last trade statistics: [Implement your logic here]")

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Bot is now running!')

def set_trade_amount(update: Update, context: CallbackContext) -> None:
    global trade_amount
    trade_amount = int(update.message.text.split()[-1])
    update.message.reply_text(f"Trade amount set to: {trade_amount}")

def create_wallet(update: Update, context: CallbackContext) -> None:
    global WALLET
    WALLET = Keypair.generate()
    update.message.reply_text(f"New wallet created.\nPublic Key: {WALLET.public_key}")

def show_wallet(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(f"Current Wallet's Public Key: {WALLET.public_key}")

if __name__ == '__main__':
    updater = Updater(token='6570174976:AAFKv6aT3ouXs46s69mJpXo847ymT
