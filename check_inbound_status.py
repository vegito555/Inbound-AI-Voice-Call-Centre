import asyncio
import os
import certifi

os.environ['SSL_CERT_FILE'] = certifi.where()

from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import ListSIPInboundTrunkRequest, ListSIPDispatchRuleRequest

load_dotenv(".env")

async def main():
    print("Connecting to LiveKit API...")
    url = os.getenv("LIVEKIT_URL")
    key = os.getenv("LIVEKIT_API_KEY")
    secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not (url and key and secret):
        print("Error: Missing LiveKit credentials in env")
        return

    lkapi = api.LiveKitAPI(url=url, api_key=key, api_secret=secret)
    
    try:
        print("\nFetching Inbound SIP Trunks...")
        response_in = await lkapi.sip.list_sip_inbound_trunk(ListSIPInboundTrunkRequest())
        trunks_in = response_in.items
        print(f"Found {len(trunks_in)} Inbound SIP Trunks:")
        for t in trunks_in:
            print(f"  ID: {t.sip_trunk_id}")
            print(f"  Name: {t.name}")
            print(f"  Numbers: {t.numbers}")
            print("-" * 20)

        print("\nFetching SIP Dispatch Rules...")
        response_rules = await lkapi.sip.list_sip_dispatch_rule(ListSIPDispatchRuleRequest())
        rules = response_rules.items
        print(f"Found {len(rules)} SIP Dispatch Rules:")
        for r in rules:
            print(f"  ID: {r.sip_dispatch_rule_id}")
            print(f"  Name: {r.name}")
            print(f"  Trunk IDs: {r.trunk_ids}")
            print(f"  Room Prefix: {r.rule.dispatch_rule_individual.room_prefix if r.rule.HasField('dispatch_rule_individual') else 'N/A'}")
            print(f"  Agents: {[a.agent_name for a in r.room_config.agents] if r.room_config else 'N/A'}")
            print("-" * 20)
            
    except Exception as e:
        print(f"Error checking inbound status: {e}")
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(main())
