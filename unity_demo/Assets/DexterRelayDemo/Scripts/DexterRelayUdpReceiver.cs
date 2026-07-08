using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

public enum DexterRelayFinger
{
    Thumb,
    Index,
    Middle,
    Ring,
    Pinky
}

[Serializable]
public class DexterRelayForceFrame
{
    public string type;
    public int version;
    public long sequence;
    public string transport;
    public DexterRelayFingers fingers;
}

[Serializable]
public class DexterRelayFingers
{
    public DexterRelayFingerMeasurement thumb;
    public DexterRelayFingerMeasurement index;
    public DexterRelayFingerMeasurement middle;
    public DexterRelayFingerMeasurement ring;
    public DexterRelayFingerMeasurement pinky;
}

[Serializable]
public class DexterRelayFingerMeasurement
{
    public int[] raw = new int[0];
    public float[] force = new float[0];
    public int channels;
    public bool has_data;
}

public class DexterRelayUdpReceiver : MonoBehaviour
{
    [Header("Relay")]
    [SerializeField] private string serverHost = "127.0.0.1";
    [SerializeField] private int serverPort = 45678;

    [Header("Protocol")]
    [SerializeField] private float subscribeIntervalSeconds = 2.0f;
    [SerializeField] private float staleAfterSeconds = 2.5f;

    [Header("Demo Overlay")]
    [SerializeField] private bool drawOverlay = true;
    [SerializeField] private Vector2 overlayPosition = new Vector2(16, 16);
    [SerializeField] private Vector2 overlaySize = new Vector2(620, 210);

    private readonly Queue<string> pendingJson = new Queue<string>();
    private readonly object pendingLock = new object();

    private UdpClient udp;
    private Thread receiveThread;
    private volatile bool running;
    private string clientId;
    private float nextSubscribeTime;
    private float lastFrameRealtime;
    private string lastError;

    public DexterRelayForceFrame LatestFrame { get; private set; }
    public bool HasRecentFrame => LatestFrame != null && Time.realtimeSinceStartup - lastFrameRealtime <= staleAfterSeconds;

    private void OnEnable()
    {
        StartReceiver();
    }

    private void Update()
    {
        if (!running)
            return;

        if (Time.realtimeSinceStartup >= nextSubscribeTime)
        {
            SendSubscribe();
            nextSubscribeTime = Time.realtimeSinceStartup + Mathf.Max(0.25f, subscribeIntervalSeconds);
        }

        DrainPendingDatagrams();
    }

    private void OnGUI()
    {
        if (!drawOverlay)
            return;

        GUILayout.BeginArea(new Rect(overlayPosition, overlaySize), GUI.skin.box);
        GUILayout.Label($"Dexter Relay UDP {serverHost}:{serverPort}");

        if (!string.IsNullOrEmpty(lastError))
            GUILayout.Label($"Error: {lastError}");

        if (LatestFrame == null)
        {
            GUILayout.Label("Waiting for force frames...");
            GUILayout.EndArea();
            return;
        }

        string freshness = HasRecentFrame ? "live" : "stale";
        GUILayout.Label($"seq={LatestFrame.sequence} transport={LatestFrame.transport} status={freshness}");
        DrawFingerLine("Thumb", GetFinger(DexterRelayFinger.Thumb));
        DrawFingerLine("Index", GetFinger(DexterRelayFinger.Index));
        DrawFingerLine("Middle", GetFinger(DexterRelayFinger.Middle));
        DrawFingerLine("Ring", GetFinger(DexterRelayFinger.Ring));
        DrawFingerLine("Pinky", GetFinger(DexterRelayFinger.Pinky));
        GUILayout.EndArea();
    }

    private void OnDisable()
    {
        StopReceiver();
    }

    private void OnApplicationQuit()
    {
        StopReceiver();
    }

    public DexterRelayFingerMeasurement GetFinger(DexterRelayFinger finger)
    {
        if (LatestFrame == null || LatestFrame.fingers == null)
            return null;

        switch (finger)
        {
            case DexterRelayFinger.Thumb:
                return LatestFrame.fingers.thumb;
            case DexterRelayFinger.Index:
                return LatestFrame.fingers.index;
            case DexterRelayFinger.Middle:
                return LatestFrame.fingers.middle;
            case DexterRelayFinger.Ring:
                return LatestFrame.fingers.ring;
            case DexterRelayFinger.Pinky:
                return LatestFrame.fingers.pinky;
            default:
                return null;
        }
    }

    private void StartReceiver()
    {
        StopReceiver();

        clientId = Guid.NewGuid().ToString("N");
        lastError = null;

        try
        {
            udp = new UdpClient(0);
            udp.Connect(serverHost, serverPort);
            udp.Client.ReceiveTimeout = 250;

            running = true;
            receiveThread = new Thread(ReceiveLoop)
            {
                IsBackground = true,
                Name = "DexterRelayUdpReceiver"
            };
            receiveThread.Start();

            nextSubscribeTime = 0.0f;
        }
        catch (Exception ex)
        {
            lastError = ex.Message;
            running = false;
            udp?.Close();
            udp = null;
        }
    }

    private void StopReceiver()
    {
        if (!running && udp == null)
            return;

        SendUnsubscribe();
        running = false;

        try
        {
            udp?.Close();
        }
        catch
        {
        }

        if (receiveThread != null && receiveThread.IsAlive)
            receiveThread.Join(500);

        receiveThread = null;
        udp = null;
    }

    private void ReceiveLoop()
    {
        var remote = new IPEndPoint(IPAddress.Any, 0);

        while (running)
        {
            try
            {
                byte[] data = udp.Receive(ref remote);
                string json = Encoding.UTF8.GetString(data);
                lock (pendingLock)
                {
                    pendingJson.Enqueue(json);
                }
            }
            catch (SocketException ex)
            {
                if (ex.SocketErrorCode != SocketError.TimedOut && running)
                    lastError = ex.Message;
            }
            catch (ObjectDisposedException)
            {
                return;
            }
            catch (Exception ex)
            {
                if (running)
                    lastError = ex.Message;
            }
        }
    }

    private void DrainPendingDatagrams()
    {
        while (true)
        {
            string json;
            lock (pendingLock)
            {
                if (pendingJson.Count == 0)
                    return;
                json = pendingJson.Dequeue();
            }

            if (!json.Contains("\"type\":\"force\"") && !json.Contains("\"type\": \"force\""))
                continue;

            try
            {
                var frame = JsonUtility.FromJson<DexterRelayForceFrame>(json);
                if (frame != null && frame.type == "force" && frame.fingers != null)
                {
                    LatestFrame = frame;
                    lastFrameRealtime = Time.realtimeSinceStartup;
                    lastError = null;
                }
            }
            catch (Exception ex)
            {
                lastError = ex.Message;
            }
        }
    }

    private void SendSubscribe()
    {
        SendPacket(BuildPacketJson("subscribe"));
    }

    private void SendUnsubscribe()
    {
        SendPacket(BuildPacketJson("unsubscribe"));
    }

    private void SendPacket(string json)
    {
        if (udp == null)
            return;

        try
        {
            byte[] data = Encoding.UTF8.GetBytes(json);
            udp.Send(data, data.Length);
        }
        catch (Exception ex)
        {
            if (running)
                lastError = ex.Message;
        }
    }

    private string BuildPacketJson(string type)
    {
        return "{\"type\":\"" + type + "\",\"version\":1,\"client\":\"unity-demo\",\"client_id\":\"" + clientId + "\"}";
    }

    private static void DrawFingerLine(string label, DexterRelayFingerMeasurement measurement)
    {
        if (measurement == null || !measurement.has_data)
        {
            GUILayout.Label($"{label}: waiting");
            return;
        }

        GUILayout.Label($"{label}: force={FormatVector(measurement.force)} N raw={FormatVector(measurement.raw)}");
    }

    private static string FormatVector(float[] values)
    {
        if (values == null || values.Length == 0)
            return "[]";

        var parts = new string[values.Length];
        for (int i = 0; i < values.Length; i++)
            parts[i] = values[i].ToString("F3");

        return "[" + string.Join(", ", parts) + "]";
    }

    private static string FormatVector(int[] values)
    {
        if (values == null || values.Length == 0)
            return "[]";

        var parts = new string[values.Length];
        for (int i = 0; i < values.Length; i++)
            parts[i] = values[i].ToString();

        return "[" + string.Join(", ", parts) + "]";
    }
}
