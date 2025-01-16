import os
import json
import asyncio
import logging
import websockets
import httpx
import argparse
import tomli
from datetime import datetime
from PIL import Image
from pathlib import Path
from openai import OpenAI
import pycspr
from typing import Optional, Dict, List, Any, Union
import typing
import sys
import aiofiles
from urllib.parse import urljoin
import hashlib

# Load configuration
CONFIG_PATH = "config.toml"
try:
    with open(CONFIG_PATH, "rb") as f:
        config = tomli.load(f)
except Exception as e:
    sys.exit(1)

# Set up logging immediately
try:
    log_config = config.get('logging', {})
    logging.basicConfig(
        level=log_config.get('level', 'DEBUG'),
        filename=log_config.get('file', 'delegation_listener.log'),
        format=log_config.get('format', '%(asctime)s - [%(filename)s:%(lineno)d] - %(levelname)s - %(message)s'),
        datefmt=log_config.get('date_format', '%Y-%m-%d %H:%M:%S'),
        force=True
    )
except Exception as e:
    sys.exit(1)

# Initialize global variables
PING_TIMEOUT = config['websocket']['ping_timeout']
MAX_RECONNECT_ATTEMPTS = config['websocket']['max_reconnect_attempts']
RECONNECT_DELAY = config['websocket']['reconnect_delay']
ping_timer = None

class CasperClient:
    """Wrapper for Casper Network interactions"""
    
    def __init__(self, node_url: str):
        connection = pycspr.NodeConnection(
            host=node_url
        )
        self.client = pycspr.NodeClient(connection)
        self.connection = connection

    def get_state_root_hash(self, block_id=None) -> bytes:
        return self.client.get_state_root_hash(block_id)

    def get_auction_info(self):
        try:
            return self.client.get_auction_info()
        except Exception as e:
            logging.error(f"Error getting auction info: {e}")
            return None

    def get_validator_changes(self):
        try:
            return self.client.get_validator_changes()
        except Exception as e:
            logging.error(f"Error getting validator changes: {e}")
            return []

    def get_node_status(self):
        try:
            return self.client.get_node_status()
        except Exception as e:
            logging.error(f"Error getting node status: {e}")
            return None

class NFTMinter:
    """Handles CEP78 NFT minting operations"""
    
    def __init__(self, node_client: CasperClient, contract_hash: str):
        self.node_client = node_client
        self.contract_hash = contract_hash.replace('hash-', '')
        self.chain_name = config['network']['name']
        self.payment_amount = config['nft']['mint_payment_amount']
        self.key_path = config['validator']['key_path']
        self.collection_name = config['nft']['collection_name']
        self.gateway_url = "https://cloudflare-ipfs.com/ipfs"

    async def prepare_metadata(self, image_hash: str, delegator: str, amount: int) -> dict:
        """Prepare NFT metadata using the IPFS hash"""
        # The subdomain appears to be unique per account, so it should be configurable
        pinata_gateway = config.get('pinata', {}).get('gateway_url', 'https://crimson-labour-cougar-964.mypinata.cloud/ipfs')
        
        metadata = {
            "name": f"Staked With {self.collection_name}",
            "description": f"Delegated {format(amount // 1000000000, ',d')} CSPR with Evamiatic Staking. Received an exclusive COWL NFT Reward",
            "asset": f"{pinata_gateway}/{image_hash}"
        }
        
        # Convert to JSON string with minimal escaping
        json_str = json.dumps(metadata, separators=(',', ':'))
        
        # The metadata should be a pure string without Python-style escaping
        return json_str

    async def mint_nft(self, metadata: dict, recipient: str) -> str:
        """Mint a new NFT using the prepared metadata"""
        try:
            secret_key_path = str(Path(self.key_path) / 'secret_key.pem')
            if not Path(secret_key_path).exists():
                raise Exception(f"Secret key file not found: {secret_key_path}")

            logging.info(f"Using secret key: {secret_key_path}")
           
            args = [
                "casper-client",
                "put-deploy",
                "--node-address", config['network']['node_url'],
                "--chain-name", self.chain_name,
                "--payment-amount", str(self.payment_amount),
                "--secret-key", secret_key_path,
                "--session-hash", self.contract_hash,
                "--session-entry-point", "mint",
                "--session-arg", f"token_owner:key='{recipient}'",
                "--session-arg", f"token_meta_data:string='{metadata}'"
            ]
            
            # Log the command for debugging
            logging.info("Executing mint command with parameters:")
            logging.info(f"Node URL: {config['network']['node_url']}")
            logging.info(f"Chain: {self.chain_name}")
            logging.info(f"Payment: {self.payment_amount}")
            logging.info(f"Contract: {self.contract_hash}")
            logging.info(f"Recipient: {recipient}")
            logging.info(f"Metadata: {metadata}")
            
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()
            
            logging.info(f"Command exit code: {process.returncode}")
            if stdout_str:
                logging.info(f"Command stdout: {stdout_str}")
            if stderr_str:
                logging.error(f"Command stderr: {stderr_str}")
            
            if process.returncode != 0:
                error_msg = stderr_str if stderr_str else "No error message provided"
                raise Exception(f"Mint command failed with exit code {process.returncode}: {error_msg}")
            
            try:
                result = json.loads(stdout_str)
            except json.JSONDecodeError:
                raise Exception(f"Failed to parse command output as JSON: {stdout_str}")
            
            deploy_hash = result.get('result', {}).get('deploy_hash')
            
            if not deploy_hash:
                raise Exception(f"Deploy hash not found in output: {result}")
                
            logging.info(f"Successfully created mint deploy with hash: {deploy_hash}")
            # await self._wait_for_deploy(deploy_hash)
            
            return deploy_hash
            
        except Exception as e:
            logging.error(f"Error minting NFT: {str(e)}")
            raise

    async def _wait_for_deploy(self, deploy_hash: str, max_attempts: int = 30, delay: int = 2) -> None:
        """Wait for deploy to be executed"""
        for attempt in range(max_attempts):
            try:
                deploy_info = self.node_client.client.get_deploy(deploy_hash)
                
                if deploy_info and deploy_info.execution_results:
                    result = deploy_info.execution_results[0]
                    if result.is_success:
                        logging.info(f"Deploy {deploy_hash} executed successfully")
                        return
                    raise Exception(f"Deploy failed: {result.error_message}")
                        
                await asyncio.sleep(delay)
                
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise Exception(f"Deploy execution check failed after {max_attempts} attempts: {e}")
                await asyncio.sleep(delay)

class PinataClient:
    """Pinata IPFS client"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_base = "https://api.pinata.cloud"
        self.headers = {
            "pinata_api_key": api_key,
            "pinata_secret_api_key": api_secret
        }
        self.max_retries = 3
        self.timeout = 60.0  # seconds

    async def upload_file(self, file_path: str) -> dict:
        """Upload a file to Pinata with retries"""
        try:
            url = f"{self.api_base}/pinning/pinFileToIPFS"
            
            async with aiofiles.open(file_path, 'rb') as f:
                file_data = await f.read()
                
            # Prepare pinata metadata
            pinata_metadata = {
                "name": os.path.basename(file_path),
                "keyvalues": {
                    "network": config['network']['name'],
                    "contract": config['nft']['contract_hash'],
                    "collection": config['nft']['collection_name']
                }
            }
                
            files = {
                'file': (os.path.basename(file_path), file_data)
            }
            
            # Add metadata to request
            data = {
                'pinataMetadata': json.dumps(pinata_metadata)
            }
            
            for attempt in range(self.max_retries):
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        logging.info(f"Uploading to Pinata (attempt {attempt + 1}/{self.max_retries})")
                        response = await client.post(
                            url,
                            headers=self.headers,
                            files=files,
                            data=data
                        )
                        response.raise_for_status()
                        result = response.json()
                        
                        logging.info(f"File uploaded to Pinata with hash: {result['IpfsHash']}")
                        return {
                            'ipfs_hash': result['IpfsHash'],
                            'name': os.path.basename(file_path)
                        }
                except httpx.ReadTimeout:
                    if attempt == self.max_retries - 1:
                        raise
                    logging.warning(f"Timeout during upload attempt {attempt + 1}, retrying...")
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                except Exception as e:
                    if attempt == self.max_retries - 1:
                        raise
                    logging.warning(f"Upload attempt {attempt + 1} failed: {str(e)}, retrying...")
                    await asyncio.sleep(2 ** attempt)

        except Exception as e:
            logging.error(f"Error uploading to Pinata: {str(e)}")
            raise

class IPFSClient:
    """Direct IPFS API client"""
    
    def __init__(self, api_url: str, api_port: int = 5001):
        self.api_base = f"{api_url}:{api_port}/api/v0"

    async def upload_file(self, file_path: str) -> dict:
        """Upload a file to IPFS"""
        try:
            # Prepare the file for upload
            async with aiofiles.open(file_path, 'rb') as f:
                file_data = await f.read()

            # Upload to IPFS
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/add",
                    files={'file': file_data}
                )
                response.raise_for_status()
                result = response.json()

                logging.info(f"File uploaded to IPFS with hash: {result['Hash']}")
                return {
                    'Hash': result['Hash'],
                    'Name': result['Name'],
                    'Size': result['Size']
                }

        except Exception as e:
            logging.error(f"Error uploading to IPFS: {e}")
            raise

def ensure_directories():
    """Ensure all required directories exist"""
    Path(config['image']['output_dir']).mkdir(parents=True, exist_ok=True)

def get_account_hash(public_key: str) -> str:
    """Convert a public key to an account hash"""
    try:
        # Create public key bytes from hex
        pub_key_bytes = bytes.fromhex(public_key)
        
        # Use the SDK's account_hash function
        account_hash = pycspr.crypto.get_account_hash(pub_key_bytes)
        
        # Convert to hex string and add prefix
        return f"account-hash-{account_hash.hex()}"
        
    except Exception as e:
        logging.error(f"Error converting public key to account hash: {e}")
        raise

async def process_image_with_template(generated_image_path: str, timestamp: str) -> str:
    try:
        generated_image = Image.open(generated_image_path)
        template_image = Image.open(config['image']['template_image'])
        box = tuple(config['image']['box_coordinates'])
        resized_image = generated_image.resize((box[2] - box[0], box[3] - box[1]))
        template_image.paste(resized_image, box)
        
        processed_image_path = os.path.join(
            config['image']['output_dir'],
            f"processed_{timestamp}.png"
        )
        
        template_image.save(processed_image_path)
        logging.debug(f"Processed image saved to {processed_image_path}")
        
        return processed_image_path
        
    except Exception as e:
        logging.error(f"Error processing image with template: {e}")
        raise

async def generate_image(client, prompt=None):
    try:
        logging.debug("Generating image with DALL-E...")
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt or config['image']['prompt'],
            size=config['image']['size'],
            quality=config['image']['quality'],
            style=config['image']['style'],
            n=1,
        )
        
        image_url = response.data[0].url
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        async with httpx.AsyncClient() as http_client:
            image_response = await http_client.get(image_url)
            image_response.raise_for_status()
            
            raw_image_path = os.path.join(
                config['image']['output_dir'],
                f"raw_{timestamp}.png"
            )
            
            with open(raw_image_path, 'wb') as f:
                f.write(image_response.content)
            
            logging.debug(f"Raw image saved to {raw_image_path}")
            processed_image_path = await process_image_with_template(raw_image_path, timestamp)
            os.remove(raw_image_path)
            logging.debug(f"Cleaned up raw image: {raw_image_path}")
            
            return processed_image_path
            
    except Exception as e:
        logging.error(f"Error generating image: {e}")
        raise

async def test_image_generation(test_prompt=None):
    ensure_directories()
    openai = OpenAI(api_key=config['openai']['api_key'])
    
    try:
        image_path = await generate_image(openai, test_prompt)
        logging.info(f"Generated and processed image saved to: {image_path}")
        return image_path
        
    except Exception as e:
        logging.error(f"Error in test_image_generation: {e}")
        raise

def heartbeat():
    global ping_timer
    logging.debug(f"Heartbeat at {datetime.now().strftime('%H:%M:%S')}")
    if ping_timer:
        ping_timer.cancel()
    ping_timer = asyncio.create_task(async_timeout())

async def async_timeout():
    try:
        await asyncio.sleep(PING_TIMEOUT / 1000)
        logging.error("Heartbeat timeout occurred")
        raise websockets.exceptions.ConnectionClosed(1000, "Heartbeat timeout")
    except asyncio.CancelledError:
        pass

async def verify_contract_setup():
    """Verify the NFT contract configuration"""
    logging.info("Verifying contract setup...")
    try:
        # Check contract hash format
        contract_hash = config['nft']['contract_hash']
        if not contract_hash.startswith('hash-'):
            logging.warning(f"Contract hash should start with 'hash-': {contract_hash}")
        
        # Verify key file exists and is readable
        key_path = Path(config['validator']['key_path'])
        key_files = list(key_path.glob('*.pem'))
        if not key_files:
            raise Exception(f"No .pem files found in {key_path}")
        logging.info(f"Found key file: {key_files[0]}")
        
        # Test if key file is readable
        with open(key_files[0], 'r') as f:
            key_content = f.read()
            if not key_content:
                raise Exception(f"Key file is empty: {key_files[0]}")
        
        # Verify payment amount
        payment_amount = int(config['nft']['mint_payment_amount'])
        if payment_amount < 1000000000:  # Less than 1 CSPR
            logging.warning(f"Payment amount might be too low: {payment_amount}")
        
        logging.info("Contract setup verification completed")
        return True
        
    except Exception as e:
        logging.error(f"Contract setup verification failed: {str(e)}")
        raise

async def test_nft_minting(test_prompt=None, test_delegator=None):
    """Test the complete NFT minting workflow"""
    logging.info("Starting NFT minting test")
    ensure_directories()
    
    try:
        # Verify contract setup first
        await verify_contract_setup()
        
        # 1. Generate and process image
        openai = OpenAI(api_key=config['openai']['api_key'])
        image_path = await generate_image(openai, test_prompt)
        logging.info(f"Generated and processed image: {image_path}")
        
        # 2. Upload to IPFS/Pinata
        storage_provider = config.get('storage', {}).get('provider', 'ipfs')
        if storage_provider == 'pinata':
            storage_client = PinataClient(config['pinata']['api_key'], config['pinata']['api_secret'])
        else:  # default to IPFS
            storage_client = IPFSClient(config['ipfs']['api_url'], config['ipfs']['api_port'])
        
        upload_result = await storage_client.upload_file(image_path)
        ipfs_hash = upload_result.get('ipfs_hash') or upload_result.get('Hash')
        logging.info(f"Image uploaded with hash: {ipfs_hash}")
        
        # 3. Initialize Casper client
        casper_client = CasperClient(config['network']['node_url'])
        
        # 4. Create NFT minter
        nft_minter = NFTMinter(
            casper_client,
            config['nft']['contract_hash']
        )
        
        # Use test delegator or default to validator's address
        if test_delegator:
            delegator_hash = get_account_hash(test_delegator)
        else:
            # Default test account
            delegator_hash = get_account_hash(config['validator']['address'])
            
        test_amount = 1000 * 1000000000  # 1000 CSPR in motes
        
        logging.info(f"Using delegator account hash: {delegator_hash}")
        
        # 5. Prepare metadata and mint
        metadata = await nft_minter.prepare_metadata(ipfs_hash, delegator_hash, test_amount)
        deploy_hash = await nft_minter.mint_nft(metadata, delegator_hash)
        
        logging.info(f"NFT minted successfully!")
        logging.info(f"Image: {image_path}")
        logging.info(f"IPFS Hash: {ipfs_hash}")
        logging.info(f"Deploy Hash: {deploy_hash}")
        logging.info(f"Gateway URL: {config['pinata']['gateway_url']}/{ipfs_hash}")
        
        return {
            'image_path': image_path,
            'ipfs_hash': ipfs_hash,
            'deploy_hash': deploy_hash
        }
        
    except Exception as e:
        logging.error(f"Error in NFT minting test: {str(e)}")
        raise

async def handle_delegation_event(event: dict, casper_client: CasperClient):
    """Handle delegation event and mint NFT reward"""
    try:
        # Verify the contract setup first
        await verify_contract_setup()
        
        validator = (event['data']['args']['validator']['parsed'] 
                    if event['extra']['entry_point_name'] == 'delegate'
                    else event['data']['args']['new_validator']['parsed'])
        
        logging.debug(f"Delegation to Validator: {validator}")
        logging.debug(f"Delegation by Delegator: {event['data']['args']['delegator']['parsed']} ")

        if validator == config['validator']['address']:
            # Get delegator public key and convert to account hash
            delegator_key = event['data']['args']['delegator']['parsed']
            delegator_hash = get_account_hash(delegator_key)
            
            motes = int(event['data']['args']['amount']['parsed'])
            cspr = format(motes // 1000000000, ',d')
            
            logging.info(f"Delegation received from {delegator_key} (hash: {delegator_hash}) for {cspr} CSPR")
            
            # 1. Generate image
            openai = OpenAI(api_key=config['openai']['api_key'])
            image_path = await generate_image(openai)
            logging.info(f"Generated image for delegator {delegator_hash}: {image_path}")
            
            # 2. Upload to IPFS/Pinata
            storage_provider = config.get('storage', {}).get('provider', 'ipfs')
            if storage_provider == 'pinata':
                storage_client = PinataClient(
                    config['pinata']['api_key'],
                    config['pinata']['api_secret']
                )
            else:  # default to IPFS
                storage_client = IPFSClient(
                    config['ipfs']['api_url'],
                    config['ipfs']['api_port']
                )
            
            upload_result = await storage_client.upload_file(image_path)
            ipfs_hash = upload_result.get('ipfs_hash') or upload_result.get('Hash')
            logging.info(f"Image uploaded with hash: {ipfs_hash}")
            
            # 3. Mint NFT using account hash
            nft_minter = NFTMinter(
                casper_client,
                config['nft']['contract_hash']
            )
            
            # Prepare metadata and mint using account hash
            metadata = await nft_minter.prepare_metadata(ipfs_hash, delegator_hash, motes)
            deploy_hash = await nft_minter.mint_nft(metadata, delegator_hash)
            
            logging.info(f"NFT minted successfully!")
            logging.info(f"Delegator public key: {delegator_key}")
            logging.info(f"Delegator account hash: {delegator_hash}")
            logging.info(f"Deploy hash: {deploy_hash}")
            
        else:
            logging.info(f"Delegation not for this validator: {config['validator']['address']}")
                
    except Exception as e:
        logging.error(f"Error handling delegation event: {e}")
        raise
                
    except Exception as e:
        logging.error(f"Error handling delegation event: {e}")
        raise

async def handle_websocket():
    ws_url = f"{config['cspr_cloud']['ws_url']}deploys?contract_package_hash={config['contracts']['auction_contract_package']}&contract_hash={config['contracts']['auction_contract']}"
    
    logging.info(f"Connecting to WebSocket: {ws_url}")
    casper_client = CasperClient(config['network']['node_url'])

    try:
        async with websockets.connect(
            ws_url,
            extra_headers={"authorization": config['cspr_cloud']['api_key']},
            ping_interval=None,
            ping_timeout=None
        ) as websocket:
            logging.debug("WebSocket connection established")
            heartbeat()
            
            while True:
                try:
                    message = await websocket.recv()
                    
                    if message == "Ping":
                        logging.debug("Received Ping")
                        heartbeat()
                        continue
                    
                    try:
                        event = json.loads(message)
                        if event.get('extra', {}).get('entry_point_name') in ['delegate', 'redelegate']:
                            logging.debug("Processing delegation event")
                            await handle_delegation_event(event, casper_client)
                    except json.JSONDecodeError:
                        logging.warning(f"Received non-JSON message")
                        continue

                except websockets.exceptions.ConnectionClosed as e:
                    logging.error(f"WebSocket connection closed: {e}")
                    raise
                except Exception as e:
                    logging.exception("Error processing message")
                    continue

    except websockets.exceptions.InvalidStatusCode as e:
        if e.status_code == 401:
            logging.error("Authentication failed: Invalid or expired API key")
            sys.exit(1)
        raise
    except Exception as e:
        logging.exception("WebSocket connection error")
    
    logging.info("Attempting to reconnect...")

async def main():
    logging.info("Starting delegation listener")
    ensure_directories()
    
    while True:
        try:
            await handle_websocket()
        except Exception as e:
            logging.exception("Connection error")
        
        logging.info(f"Reconnecting in {RECONNECT_DELAY} seconds")
        await asyncio.sleep(RECONNECT_DELAY)

def parse_arguments():
    parser = argparse.ArgumentParser(description='Delegation Listener and Image Generation Script')
    parser.add_argument('--test-image', action='store_true', 
                      help='Run image generation test only')
    parser.add_argument('--test-mint', action='store_true',
                      help='Test complete NFT minting process')
    parser.add_argument('--custom-prompt', type=str,
                      help='Custom prompt for image generation')
    parser.add_argument('--test-delegator', type=str,
                      help='Test delegator address for NFT minting')
    return parser.parse_args()

if __name__ == "__main__":
    try:
        args = parse_arguments()
        if args.test_image:
            logging.info("Running image generation test")
            asyncio.run(test_image_generation(args.custom_prompt))
        elif args.test_mint:
            logging.info("Running NFT minting test")
            asyncio.run(test_nft_minting(args.custom_prompt, args.test_delegator))
        else:
            logging.info("Starting delegation listener")
            asyncio.run(main())
    except Exception as e:
        logging.exception("Fatal error in main execution")
        sys.exit(1)
