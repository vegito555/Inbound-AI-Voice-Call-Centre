import asyncio
import os
import certifi

# Fix for SSL
os.environ['SSL_CERT_FILE'] = certifi.where()

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env")

async def main():
    print("Connecting to LiveKit API...")
    url = os.getenv("LIVEKIT_URL")
    key = os.getenv("LIVEKIT_API_KEY")
    secret = os.getenv("LIVEKIT_API_SECRET")

    # SIP Credentials
    sip_address = os.getenv("VOBIZ_SIP_DOMAIN")
    username = os.getenv("VOBIZ_USERNAME")
    password = os.getenv("VOBIZ_PASSWORD")
    number = os.getenv("VOBIZ_INBOUND_NUMBER") or os.getenv("VOBIZ_OUTBOUND_NUMBER")

    if not (url and key and secret):
        print("Error: Missing LiveKit credentials")
        return

    if not (sip_address and username and password and number):
        print("Error: Missing SIP credentials (VOBIZ_SIP_DOMAIN, VOBIZ_USERNAME, VOBIZ_PASSWORD, number)")
        return

    lkapi = api.LiveKitAPI(url=url, api_key=key, api_secret=secret)

    try:
        print(f"Creating SIP Inbound Trunk for {number}...")
        
        trunk_info = api.SIPInboundTrunkInfo(
            name="Vobiz Inbound Trunk",
            numbers=[number],
            auth_username=username,
            auth_password=password,
        )

        request = api.CreateSIPInboundTrunkRequest(trunk=trunk_info)
        trunk = await lkapi.sip.create_sip_inbound_trunk(request)
        trunk_id = trunk.sip_trunk_id
        
        print("\n✅ SIP Inbound Trunk Created Successfully!")
        print(f"Trunk ID: {trunk_id}")
        
        print("\nCreating Inbound SIP Dispatch Rule...")
        rule = api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                room_prefix="inbound-",
            )
        )
        dispatch_rule_req = api.CreateSIPDispatchRuleRequest(
            name="Inbound Dispatch Rule",
            trunk_ids=[trunk_id],
            rule=rule,
            room_config=api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name="inbound-caller")]
            )
        )
        dispatch_rule = await lkapi.sip.create_sip_dispatch_rule(dispatch_rule_req)
        
        print("\n✅ SIP Dispatch Rule Created Successfully!")
        print(f"Rule ID: {dispatch_rule.sip_dispatch_rule_id}")
        print(f"Rule Name: {dispatch_rule.name}")
        print(f"Trunk IDs: {dispatch_rule.trunk_ids}")
        print("-" * 40)
        print("Please save the Trunk ID in your environment variables as INBOUND_TRUNK_ID.")
        
    except Exception as e:
        print(f"\n❌ Error creating trunk/rule: {e}")
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(main())
