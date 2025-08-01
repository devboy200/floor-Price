import os
import discord
import time
import asyncio
import logging
import subprocess
import requests
import zipfile
import stat
import shutil
import random
import json
import base58
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from discord.ext import tasks, commands
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Fetch and validate environment variables
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
PRICE_DISCUSSION_CHANNEL_ID = os.getenv("PRICE_DISCUSSION_CHANNEL_ID")
PRICE_FEED_CHANNEL_ID = os.getenv("PRICE_FEED_CHANNEL_ID")

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ DISCORD_BOT_TOKEN is not set in environment variables.")
if not PRICE_DISCUSSION_CHANNEL_ID:
    raise ValueError("❌ PRICE_DISCUSSION_CHANNEL_ID is not set in environment variables.")
if not PRICE_FEED_CHANNEL_ID:
    raise ValueError("❌ PRICE_FEED_CHANNEL_ID is not set in environment variables.")

try:
    PRICE_DISCUSSION_CHANNEL_ID = int(PRICE_DISCUSSION_CHANNEL_ID)
    PRICE_FEED_CHANNEL_ID = int(PRICE_FEED_CHANNEL_ID)
except ValueError:
    raise ValueError("❌ Channel IDs must be valid integers.")

# Bot constants
ANA_TOKEN_CONTRACT = "5DkzT65YJvCsZcot9L6qwkJnsBCPmKHjJz3QU7t7QeRW"
TEAM_WALLET = "BcAoCEdkzV2J21gAjCCEokBw5iMnAe96SbYo9F6QmKWV"
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

# Setup Discord bot with slash commands
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Global variables for tracking
last_ana_price = None
last_prana_price = None
last_floor_price = None
last_signature = None

def find_chrome_binary():
    """Find Chrome/Chromium binary location - copied from your working code"""
    railway_chrome = os.environ.get("GOOGLE_CHROME_BIN")
    if railway_chrome and os.path.exists(railway_chrome):
        logger.info(f"✅ Found Railway Chrome binary: {railway_chrome}")
        return railway_chrome
    
    possible_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/chrome",
        "/opt/google/chrome/google-chrome",
        "/app/.chrome-for-testing/chrome-linux64/chrome"
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"✅ Found Chrome binary: {path}")
            return path
    
    logger.error("❌ No Chrome binary found")
    return None

def get_chrome_version(chrome_path):
    """Get Chrome version - copied from your working code"""
    try:
        result = subprocess.run([chrome_path, "--version"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_output = result.stdout.strip()
            logger.info(f"✅ Chrome version: {version_output}")
            
            version_parts = version_output.split()
            version_number = version_parts[-1]
            major_version = version_number.split('.')[0]
            
            logger.info(f"✅ Chrome major version: {major_version}")
            return version_number, major_version
        else:
            logger.error(f"❌ Failed to get Chrome version: {result.stderr}")
            return None, None
    except Exception as e:
        logger.error(f"❌ Error getting Chrome version: {e}")
        return None, None

def download_compatible_chromedriver(major_version):
    """Download ChromeDriver - copied from your working code"""
    try:
        railway_chromedriver = os.environ.get("CHROMEDRIVER_PATH")
        if railway_chromedriver and os.path.exists(railway_chromedriver):
            logger.info(f"✅ Using Railway ChromeDriver: {railway_chromedriver}")
            return railway_chromedriver
        
        driver_dir = "/tmp/chromedriver_new"
        driver_path = os.path.join(driver_dir, "chromedriver")
        
        if os.path.exists(driver_dir):
            shutil.rmtree(driver_dir)
        
        os.makedirs(driver_dir, exist_ok=True)
        
        logger.info(f"📥 Downloading ChromeDriver for Chrome {major_version}...")
        
        if int(major_version) >= 115:
            try:
                api_url = f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major_version}"
                logger.info(f"🔍 Checking API: {api_url}")
                
                response = requests.get(api_url, timeout=30)
                if response.status_code == 200:
                    driver_version = response.text.strip()
                    logger.info(f"✅ Found ChromeDriver version: {driver_version}")
                    download_url = f"https://storage.googleapis.com/chrome-for-testing-public/{driver_version}/linux64/chromedriver-linux64.zip"
                else:
                    logger.warning(f"⚠️ API returned {response.status_code}, using fallback version")
                    if major_version == "138":
                        driver_version = "138.0.6906.100"
                    else:
                        driver_version = f"{major_version}.0.6000.0"
                    download_url = f"https://storage.googleapis.com/chrome-for-testing-public/{driver_version}/linux64/chromedriver-linux64.zip"
                    
            except Exception as e:
                logger.warning(f"⚠️ New API failed: {e}, using fallback")
                if major_version == "138":
                    driver_version = "138.0.6906.100"
                else:
                    driver_version = f"{major_version}.0.6000.0"
                download_url = f"https://storage.googleapis.com/chrome-for-testing-public/{driver_version}/linux64/chromedriver-linux64.zip"
        else:
            api_url = f"https://chromedriver.storage.googleapis.com/LATEST_RELEASE_{major_version}"
            try:
                response = requests.get(api_url, timeout=30)
                if response.status_code == 200:
                    driver_version = response.text.strip()
                    download_url = f"https://chromedriver.storage.googleapis.com/{driver_version}/chromedriver_linux64.zip"
                else:
                    raise Exception(f"Old API returned status {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Failed to get ChromeDriver version for Chrome {major_version}: {e}")
                return None
        
        logger.info(f"📥 Downloading ChromeDriver {driver_version} from: {download_url}")
        
        zip_path = os.path.join(driver_dir, "chromedriver.zip")
        
        try:
            response = requests.get(download_url, timeout=120)
            response.raise_for_status()
            
            with open(zip_path, 'wb') as f:
                f.write(response.content)
            
            logger.info("📂 Extracting ChromeDriver...")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(driver_dir)
            
            chromedriver_found = False
            for root, dirs, files in os.walk(driver_dir):
                for file in files:
                    if file == "chromedriver":
                        extracted_path = os.path.join(root, file)
                        if extracted_path != driver_path:
                            shutil.move(extracted_path, driver_path)
                        chromedriver_found = True
                        break
                if chromedriver_found:
                    break
            
            if not chromedriver_found:
                logger.error("❌ ChromeDriver executable not found in downloaded files")
                return None
            
            os.chmod(driver_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            os.remove(zip_path)
            
            logger.info("🧪 Testing downloaded ChromeDriver...")
            result = subprocess.run([driver_path, "--version"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info(f"✅ ChromeDriver working: {result.stdout.strip()}")
                return driver_path
            else:
                logger.error(f"❌ Downloaded ChromeDriver test failed: {result.stderr}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"❌ Failed to download ChromeDriver: {e}")
            return None
            
    except Exception as e:
        logger.error(f"❌ ChromeDriver download error: {e}")
        return None

def setup_chromedriver_and_chrome():
    """Setup ChromeDriver - copied from your working code"""
    try:
        chrome_binary = find_chrome_binary()
        if not chrome_binary:
            logger.error("❌ Chrome binary not found")
            return None, None
        
        chrome_version, major_version = get_chrome_version(chrome_binary)
        if not chrome_version or not major_version:
            logger.error("❌ Could not determine Chrome version")
            return None, None
        
        logger.info("📥 Downloading compatible ChromeDriver...")
        chromedriver_path = download_compatible_chromedriver(major_version)
        
        if not chromedriver_path:
            logger.error("❌ Could not download compatible ChromeDriver")
            return None, None
        
        logger.info(f"✅ ChromeDriver setup complete: {chromedriver_path}")
        return chromedriver_path, chrome_binary
            
    except Exception as e:
        logger.error(f"❌ ChromeDriver setup error: {e}")
        return None, None

def create_chrome_options(chrome_binary):
    """Create Chrome options - copied from your working code"""
    options = Options()
    
    options.binary_location = chrome_binary
    
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222")
    
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--memory-pressure-off")
    
    options.add_argument("--max_old_space_size=4096")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-dev-tools")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    return options

def wait_for_page_ready(driver, timeout=60):
    """Wait for page ready - copied from your working code"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        time.sleep(3)
        
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return typeof jQuery === 'undefined' || jQuery.active === 0")
            )
        except:
            pass
        
        logger.info("✅ Page fully loaded")
        return True
        
    except TimeoutException:
        logger.warning("⚠️ Page load timeout, continuing anyway")
        return False

def fetch_nirvana_data(url, data_type):
    """Fetch data from Nirvana Finance pages"""
    driver = None
    
    try:
        logger.info(f"🔄 Fetching {data_type} from {url}")
        
        chromedriver_path, chrome_binary = setup_chromedriver_and_chrome()
        if not chromedriver_path or not chrome_binary:
            logger.error("❌ Chrome/ChromeDriver setup failed")
            return None
        
        options = create_chrome_options(chrome_binary)
        service = Service(executable_path=chromedriver_path)
        
        logger.info("🚀 Starting Chrome WebDriver...")
        driver = webdriver.Chrome(service=service, options=options)
        
        driver.set_page_load_timeout(120)
        driver.implicitly_wait(10)
        
        logger.info(f"🌐 Loading {url}...")
        driver.get(url)
        
        logger.info("⏳ Waiting for page to be fully loaded...")
        wait_for_page_ready(driver, timeout=90)
        
        time.sleep(10)
        
        wait = WebDriverWait(driver, 60)
        
        selectors_to_try = [
            ("CLASS_NAME", "DataPoint_dataPointValue__Bzf_E"),
            ("CSS_SELECTOR", "[class*='DataPoint_dataPointValue']"),
            ("CSS_SELECTOR", "[class*='dataPointValue']"),
        ]
        
        data_text = None
        
        for selector_type, selector in selectors_to_try:
            try:
                logger.info(f"🔍 Trying {selector_type}: {selector}")
                
                if selector_type == "CLASS_NAME":
                    elements = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, selector)))
                elif selector_type == "CSS_SELECTOR":
                    elements = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector)))
                
                # For mint page, get first element (ANA price)
                # For realize page, we need to identify which element based on context
                if "mint" in url:
                    element = elements[0] if elements else None
                elif "realize" in url:
                    # Floor price is typically first, prANA might be second
                    if data_type == "floor_price":
                        element = elements[0] if elements else None
                    elif data_type == "prana_price":
                        element = elements[1] if len(elements) > 1 else None
                    else:
                        element = elements[0] if elements else None
                else:
                    element = elements[0] if elements else None
                
                if element:
                    wait.until(EC.visibility_of(element))
                    time.sleep(5)
                    
                    data_text = element.text.strip()
                    logger.info(f"📝 Found {data_type}: '{data_text}'")
                    
                    if data_text and data_text != "":
                        break
                        
            except TimeoutException:
                logger.debug(f"⏳ {selector_type} '{selector}' timed out")
                continue
            except Exception as e:
                logger.debug(f"⚠️ {selector_type} '{selector}' failed: {e}")
                continue
        
        if data_text:
            # Clean the data
            cleaned_data = data_text.replace("USDC", "").replace("$", "").replace(",", "").strip()
            
            logger.info(f"🧹 Cleaned '{data_text}' to '{cleaned_data}'")
            
            if cleaned_data:
                try:
                    float(cleaned_data)
                    logger.info(f"✅ Valid {data_type} extracted: {cleaned_data}")
                    return cleaned_data
                except ValueError:
                    logger.warning(f"⚠️ Invalid number format: '{cleaned_data}'")
                    return None
            else:
                logger.warning(f"⚠️ {data_type} text empty after cleaning")
                return None
        else:
            logger.warning(f"⚠️ No {data_type} found")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error fetching {data_type}: {e}")
        return None
        
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("🔄 Chrome WebDriver closed")
            except Exception as close_error:
                logger.warning(f"⚠️ Error closing WebDriver: {close_error}")

async def get_solana_transactions():
    """Monitor Solana blockchain for ANA mint transactions"""
    try:
        headers = {"Content-Type": "application/json"}
        
        # Get recent signatures for the token account
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                ANA_TOKEN_CONTRACT,
                {
                    "limit": 10,
                    "commitment": "confirmed"
                }
            ]
        }
        
        response = requests.post(SOLANA_RPC_URL, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if "result" in data and data["result"]:
                signatures = data["result"]
                return signatures
        
        return []
        
    except Exception as e:
        logger.error(f"❌ Error fetching Solana transactions: {e}")
        return []

async def analyze_transaction(signature):
    """Analyze a transaction to detect ANA buys"""
    try:
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {
                    "encoding": "json",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0
                }
            ]
        }
        
        response = requests.post(SOLANA_RPC_URL, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            if "result" in data and data["result"]:
                transaction = data["result"]
                
                # Look for mint transactions
                if transaction.get("meta", {}).get("err") is None:
                    # Check if this is a mint transaction by looking at the instructions
                    instructions = transaction.get("transaction", {}).get("message", {}).get("instructions", [])
                    
                    for instruction in instructions:
                        # Look for token program instructions that might be mints
                        program_id_index = instruction.get("programIdIndex")
                        if program_id_index is not None:
                            accounts = transaction.get("transaction", {}).get("message", {}).get("accountKeys", [])
                            if program_id_index < len(accounts):
                                program_id = accounts[program_id_index]
                                
                                # Check if this is a token program
                                if program_id in ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "11111111111111111111111111111112"]:
                                    # Analyze account changes to find the buyer
                                    pre_balances = transaction.get("meta", {}).get("preBalances", [])
                                    post_balances = transaction.get("meta", {}).get("postBalances", [])
                                    
                                    for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                                        if pre > post and i < len(accounts):  # SOL was spent
                                            buyer_account = accounts[i]
                                            
                                            # Exclude team wallet
                                            if buyer_account != TEAM_WALLET:
                                                sol_spent = (pre - post) / 1000000000  # Convert lamports to SOL
                                                
                                                if sol_spent > 0.01:  # Minimum threshold to avoid tiny transactions
                                                    logger.info(f"🎯 Buy detected: {buyer_account} spent {sol_spent} SOL")
                                                    
                                                    # Get current SOL price for USD conversion
                                                    sol_price_usd = await get_sol_price()
                                                    usd_amount = sol_spent * sol_price_usd if sol_price_usd else 0
                                                    
                                                    return {
                                                        "buyer": buyer_account,
                                                        "sol_amount": sol_spent,
                                                        "usd_amount": usd_amount,
                                                        "signature": signature
                                                    }
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Error analyzing transaction {signature}: {e}")
        return None

async def get_sol_price():
    """Get current SOL price in USD"""
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("solana", {}).get("usd", 150)  # Default fallback
        return 150  # Fallback price
    except:
        return 150  # Fallback price

@bot.slash_command(name="price", description="Get current ANA market update")
async def price_command(ctx):
    """Slash command to show ANA market data"""
    if ctx.channel.id != PRICE_DISCUSSION_CHANNEL_ID:
        await ctx.respond("❌ This command can only be used in the price discussion channel.", ephemeral=True)
        return
    
    await ctx.defer()
    
    try:
        # Fetch all price data
        logger.info("📊 Fetching ANA market data...")
        
        # Fetch data in parallel using asyncio
        loop = asyncio.get_event_loop()
        
        ana_price_task = loop.run_in_executor(None, fetch_nirvana_data, "https://mainnet.nirvana.finance/mint", "ana_price")
        floor_price_task = loop.run_in_executor(None, fetch_nirvana_data, "https://mainnet.nirvana.finance/realize", "floor_price")
        prana_price_task = loop.run_in_executor(None, fetch_nirvana_data, "https://mainnet.nirvana.finance/realize", "prana_price")
        
        ana_price, floor_price, prana_price = await asyncio.gather(ana_price_task, floor_price_task, prana_price_task)
        
        if not ana_price:
            ana_price = "N/A"
        if not floor_price:
            floor_price = "N/A"
        if not prana_price:
            prana_price = "N/A"
        
        # Format the message
        message = f"""🧠 **ANA Market Update** 
- **ANA Price:** ${ana_price}
- **prANA Price:** ${prana_price}
- **Floor Price:** ${floor_price}
Powered by Nirvana Protocol. Stay informed, stay sharp. ⚡"""
        
        await ctx.followup.send(message)
        logger.info("✅ Price command executed successfully")
        
    except Exception as e:
        logger.error(f"❌ Price command error: {e}")
        await ctx.followup.send("❌ Error fetching price data. Please try again later.")

@tasks.loop(seconds=60)  # Check every minute
async def monitor_transactions():
    """Monitor for new ANA buy transactions"""
    global last_signature
    
    try:
        logger.info("🔍 Monitoring for new ANA transactions...")
        
        signatures = await get_solana_transactions()
        
        if signatures:
            latest_signature = signatures[0]["signature"]
            
            # Check if this is a new transaction
            if last_signature and latest_signature != last_signature:
                # Check the latest few transactions for buys
                for sig_data in signatures[:3]:  # Check last 3 transactions
                    signature = sig_data["signature"]
                    
                    if signature == last_signature:
                        break  # We've reached transactions we've already processed
                    
                    buy_data = await analyze_transaction(signature)
                    
                    if buy_data:
                        # Send buy alert
                        channel = bot.get_channel(PRICE_FEED_CHANNEL_ID)
                        if channel:
                            # Truncate wallet address for display
                            wallet_display = f"{buy_data['buyer'][:3]}...{buy_data['buyer'][-3:]}"
                            
                            message = f"""🚨 ANA Buy Detected
Wallet `{wallet_display}` just bought ANA for **{buy_data['sol_amount']:.2f} SOL** (~${buy_data['usd_amount']:.0f}).
This isn't just another buy — it's a strong signal.  
ANA's floor remains unshaken 🛡️"""
                            
                            await channel.send(message)
                            logger.info(f"🚨 Buy alert sent for {buy_data['sol_amount']:.2f} SOL")
            
            last_signature = latest_signature
            
    except Exception as e:
        logger.error(f"❌ Transaction monitoring error: {e}")

@tasks.loop(seconds=300)  # Check every 5 minutes
async def monitor_floor_price():
    """Monitor for floor price increases"""
    global last_floor_price
    
    try:
        logger.info("📈 Checking floor price...")
        
        loop = asyncio.get_event_loop()
        current_floor = await loop.run_in_executor(None, fetch_nirvana_data, "https://mainnet.nirvana.finance/realize", "floor_price")
        
        if current_floor and current_floor != "N/A":
            current_floor_float = float(current_floor)
            
            if last_floor_price and current_floor_float > last_floor_price:
                # Floor price increased!
                channel = bot.get_channel(PRICE_FEED_CHANNEL_ID)
                if channel:
                    message = f"""📈 **Floor Price Update**
The ANA floor just moved to **${current_floor}**.
your profit is lockin forever"""
                    
                    await channel.send(message)
                    logger.info(f"📈 Floor price increase alert sent: ${current_floor}")
            
            last_floor_price = current_floor_float
            
    except Exception as e:
        logger.error(f"❌ Floor price monitoring error: {e}")

@bot.event
async def on_ready():
    """Bot ready event"""
    logger.info(f"✅ ANA Bot logged in: {bot.user}")
    logger.info(f"🎯 Price Discussion Channel: {PRICE_DISCUSSION_CHANNEL_ID}")
    logger.info(f"🎯 Price Feed Channel: {PRICE_FEED_CHANNEL_ID}")
    logger.info(f"🏠 Connected to {len(bot.guilds)} servers")
    
    # Verify channels
    price_discussion_channel = bot.get_channel(PRICE_DISCUSSION_CHANNEL_ID)
    price_feed_channel = bot.get_channel(PRICE_FEED_CHANNEL_ID)
    
    if price_discussion_channel:
        logger.info(f"✅ Price Discussion Channel: '{price_discussion_channel.name}' in '{price_discussion_channel.guild.name}'")
    else:
        logger.error(f"❌ Price Discussion Channel {PRICE_DISCUSSION_CHANNEL_ID} not found!")
    
    if price_feed_channel:
        logger.info(f"✅ Price Feed Channel: '{price_feed_channel.name}' in '{price_feed_channel.guild.name}'")
    else:
        logger.error(f"❌ Price Feed Channel {PRICE_FEED_CHANNEL_ID} not found!")
    
    # Test system setup
    logger.info("🧪 Testing system setup...")
    chrome_binary = find_chrome_binary()
    if chrome_binary:
        get_chrome_version(chrome_binary)
    
    # Start monitoring tasks
    logger.info("🚀 Starting monitoring tasks...")
    monitor_transactions.start()
    monitor_floor_price.start()

@bot.event
async def on_disconnect():
    logger.warning("⚠️ Discord disconnected")

@bot.event
async def on_resumed():
    logger.info("🔄 Discord reconnected")

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"❌ Discord error in {event}")

def main():
    """Main function"""
    logger.info("🚀 🧠 ANA Discord Bot Starting...")
    logger.info(f"🐍 Python: {os.sys.version}")
    logger.info(f"📁 Working dir: {os.getcwd()}")
    logger.info(f"🚂 Platform: Railway" if "RAILWAY_ENVIRONMENT" in os.environ else "🖥️ Platform: Local")
    
    # Validate environment
    if DISCORD_BOT_TOKEN:
        logger.info("✅ Discord token configured")
    if PRICE_DISCUSSION_CHANNEL_ID:
        logger.info(f"✅ Price Discussion Channel ID: {PRICE_DISCUSSION_CHANNEL_ID}")
    if PRICE_FEED_CHANNEL_ID:
        logger.info(f"✅ Price Feed Channel ID: {PRICE_FEED_CHANNEL_ID}")
    
    # Start bot
    try:
        logger.info("🤖 Starting ANA Discord bot...")
        bot.run(DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as start_error:
        logger.error(f"❌ Bot start failed: {start_error}")
        raise

if __name__ == "__main__":
    main()
