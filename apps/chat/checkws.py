import asyncio
import websockets


async def connect_to_websocket():

    uri = "ws://127.0.0.1:8000/ws/chat/global/"

    try:
        async with websockets.connect(uri) as websocket:
            while True:
                message = input("Enter your message: ")
                await websocket.send(message)
                print(f"Sent: {message}")

    except Exception as e:
        print(f"Connection closed: {e}")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(connect_to_websocket())
