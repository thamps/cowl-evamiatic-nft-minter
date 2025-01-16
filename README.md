# COWL Evamiatic NFT Minting Service

This service automatically mints NFTs for Casper Network delegators using DALL-E generated images and IPFS storage.

## Prerequisites

- Python 3.8+
- Casper Network account and keys
- OpenAI API key
- Pinata or IPFS account (for image storage)
- CSPR.cloud API key

## Installation

1. Clone the repository and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install tomli openai httpx websockets pycspr pillow aiofiles
```

2. Copy `config.toml.template` to `config.toml` and configure your settings:
```bash
cp config.toml.template config.toml
```

3. Update the following key configuration values in `config.toml`:

- `[pinata]`: Your Pinata API credentials and gateway URL
  ```toml
  api_key = "your-pinata-api-key"
  api_secret = "your-pinata-secret"
  ```

- `[nft]`: Your CEP-78 contract details
  ```toml
  contract_hash = "your-contract-hash"
  contract_package_hash = "your-contract-package-hash"
  ```

- `[validator]`: Your validator details
  ```toml
  address = "your-validator-public-key"
  key_path = "./testnet-keys"  # Directory containing your key files
  ```

- `[cspr_cloud]`: Your CSPR.cloud credentials
  ```toml
  api_key = "your-api-key"
  ```

- `[openai]`: Your OpenAI API key
  ```toml
  api_key = "your-openai-api-key"
  ```

- `[image]`: Configure your image generation settings
  ```toml
  prompt = "your image prompt here"
  template_image = "./template.png"  # Path to your template image
  ```

4. Ensure your key files are in the directory specified by `key_path`.

## Usage

### Start the Delegation Listener

```bash
python cowl-evamiatic-nft-mint.py
```

### Test Image Generation

```bash
python cowl-evamiatic-nft-mint.py --test-image
```

### Test NFT Minting

```bash
python cowl-evamiatic-nft-mint.py --test-mint
```

Optional parameters:
- `--custom-prompt`: Specify a custom DALL-E prompt
- `--test-delegator`: Specify a test delegator address

### Monitor Logs

The script will create a log file as specified in your config (default: `delegation_listener.log`). The logging configuration supports:
- Custom log levels (INFO, DEBUG, etc.)
- Custom date formats
- Custom message formats

## Network Configuration

The script is configured by default for the Casper testnet:
- Network: `casper-test`
- Node URL: `https://node.testnet.casper.network`
- WebSocket URL: `wss://streaming.testnet.cspr.cloud/`

To switch to mainnet, update the relevant URLs in your config.toml.

## Storage Options

The service supports two storage providers:
1. Pinata (default)
2. Local IPFS node

To use a local IPFS node, update the `storage.provider` setting to "ipfs" and configure the IPFS section with your node details.

## Notes

- Ensure sufficient CSPR balance for NFT minting operations (default: 3.5 CSPR per mint)
- Keep your API keys secure and never commit them to version control
- The script includes automatic reconnection logic for WebSocket disconnections
- Default image size is 1024x1024 with "standard" quality and "vivid" style
- The WebSocket connection has a ping timeout of 13 seconds with 5 reconnection attempts
- Template image coordinates can be adjusted in the config to control image placement
