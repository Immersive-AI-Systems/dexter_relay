# Unity Dexter Relay Demo

This folder contains a minimal Unity receiver for the `dexter-relay` UDP protocol.

It is intentionally just a drop-in asset folder, not a complete Unity project.

## Setup

1. Copy `unity_demo/Assets/DexterRelayDemo` into your Unity project's `Assets/` folder.
2. Create an empty GameObject in your scene, for example `DexterRelayReceiver`.
3. Add the `DexterRelayUdpReceiver` component to that GameObject.
4. Set `Server Host` to the relay server's IP address.
5. Keep `Server Port` at `45678` unless you changed the relay server port.
6. Press Play.

The component sends UDP `subscribe` packets, receives `force` frames, and draws a simple on-screen overlay with the latest values. On disable, destroy, or application quit, it sends a best-effort UDP `unsubscribe`.

## Relay Server

On the machine connected to the Dexter device:

```bash
python -m dexter_relay.server
```

For local Unity testing without Dexter hardware:

```bash
python -m dexter_relay.server --simulate
```

The relay binds `0.0.0.0:45678` by default, so Unity clients on other computers can subscribe using the relay machine's LAN IP address.

## Multiple Unity Clients

Multiple Unity players/editors can connect to the same relay. Each instance uses its own UDP source port, and the relay sends each subscribed client the same force stream.

## Using Values In Your Own Script

You can reference the receiver and read the latest frame:

```csharp
using UnityEngine;

public class DexterExampleConsumer : MonoBehaviour
{
    public DexterRelayUdpReceiver receiver;

    void Update()
    {
        if (receiver == null || receiver.LatestFrame == null)
            return;

        var thumb = receiver.GetFinger(DexterRelayFinger.Thumb);
        if (thumb != null && thumb.has_data && thumb.force.Length >= 2)
        {
            Debug.Log($"Thumb Fx={thumb.force[0]:F3} N, Fy={thumb.force[1]:F3} N");
        }
    }
}
```

BLE frames provide two force components per finger: `force[0] = Fx`, `force[1] = Fy`. Serial 4-channel frames may provide `force[2] = Fz`.
