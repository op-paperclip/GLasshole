#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_heap_caps.h>

#include <LilyGo_Wristband.h>
#include <LV_Helper.h>
#include <lvgl.h>

LilyGo_Class amoled;

#define AP_SSID     "GLasshole"
#define AP_PASSWORD "IWannaBeAGlasshole"
#define HOSTNAME    "GLasshole"

String configuredSSID = "";
String configuredPassword = "";

WebServer webServer(80);

bool webServerStarted = false;
bool wifiConnectInProgress = false;
unsigned long wifiConnectStartMs = 0;

#define WIFI_CONNECT_TIMEOUT_MS 15000


// ============================================================
// TCP
// ============================================================

// TCP-Portrange: 8090 ... 8099
#define TCP_PORT_MIN 8090
#define TCP_PORT_MAX 8099

uint16_t currentTcpPort = TCP_PORT_MIN;

WiFiServer server(TCP_PORT_MIN);

WiFiClient client;

// Physischer BOOT-Button
LilyGo_Button bootPin;


// ============================================================
// DISPLAY
// ============================================================

// Resolution is 126 x 126.
#define DISPLAY_WIDTH   126
#define DISPLAY_HEIGHT  126

#define DISPLAY_PIXELS \
    (DISPLAY_WIDTH * DISPLAY_HEIGHT)


// ============================================================
// PROTOCOL
// ============================================================

#define PXUP_MAGIC 0x50555850UL
// "PXUP" as Little-Endian uint32_t

#define PXUR_MAGIC 0x52555850UL
// "PXUR" as Little-Endian uint32_t

#define PXUP_VERSION 0x02

#define PXUR_VERSION 0x01

#define PACKET_HEADER_SIZE 11

// After 2000ms regard frame as received and show it on display
#define FRAME_IDLE_TIMEOUT_US 2000


// ============================================================
// TCP Packet Buffer
// ============================================================

#define TCP_PACKET_BUFFER_SIZE 16000

uint8_t *tcpPacketBuffer = nullptr;


// ============================================================
// Framebuffer
// ============================================================

// Using two Buffers: committedFramebuffer and workingFramebuffer

uint16_t *committedFramebuffer = nullptr;

uint16_t *workingFramebuffer = nullptr;


// ============================================================
// LVGL Buffer
// ============================================================

lv_color_t *lvBuffer = nullptr;

lv_obj_t *canvas = nullptr;


// ============================================================
// Frame Status
// ============================================================

bool currentFrameValid = false;

uint32_t currentFrameId = 0;

// Zeitpunkt des zuletzt vollständig empfangenen Packets.
uint32_t lastFramePacketRxUs = 0;


// ============================================================
// TCP Status
// ============================================================

bool tcpConnected = false;


// ============================================================
// change TCP-Port
// ============================================================

// Change listening Port of TCP server by using the physical button. Each click increments the port by 1 up to 10 times. After 10 times of clicking, start again with 8090
// 8090 -> 8099 -> 8090

void switchTcpPort()
{
    // kill current connection
    if (client)
    {
        client.stop();
    }

    tcpConnected = false;

    // discard frame if incomplete
    currentFrameValid = false;
    lastFramePacketRxUs = 0;

    // select next port
    currentTcpPort++;

    if (currentTcpPort > TCP_PORT_MAX)
    {
        currentTcpPort = TCP_PORT_MIN;
    }

    // restart TCP-Server with new listening port
    server.end();
    delay(10);
    server.begin(currentTcpPort);

    Serial.println();
    Serial.println("======================================");
    Serial.print("TCP-Port changed to: ");
    Serial.println(currentTcpPort);
    Serial.println("======================================");
}


// ============================================================
// Button Callback
// ============================================================

void button_event_callback(ButtonState state)
{
    if (state == BTN_CLICK_EVENT)
    {
        switchTcpPort();
    }
}


// ============================================================
// Statistics
// ============================================================

uint32_t frameCounter = 0;

uint32_t packetCounter = 0;

uint32_t bytesTotal = 0;

uint32_t pixelUpdateCounter = 0;

unsigned long statisticsStart = 0;


// ============================================================
// Performance
// ============================================================

uint32_t lastPacketTimeUs = 0;

uint32_t lastDisplayTimeUs = 0;

uint32_t lastFrameTimeUs = 0;


// ============================================================
// read Little-Endian
// ============================================================

uint16_t readLE16(
    const uint8_t *p
)
{
    return
        (uint16_t)p[0] |
        ((uint16_t)p[1] << 8);
}


uint32_t readLE32(
    const uint8_t *p
)
{
    return
        (uint32_t)p[0] |
        ((uint32_t)p[1] << 8) |
        ((uint32_t)p[2] << 16) |
        ((uint32_t)p[3] << 24);
}


// ============================================================
// read TCP exactly
// ============================================================

bool receiveExact(
    WiFiClient &c,
    uint8_t *buffer,
    size_t length,
    uint32_t timeoutMs = 5000
)
{
    size_t received = 0;

    unsigned long start =
        millis();


    while (received < length)
    {
        if (!c.connected())
        {
            return false;
        }


        int available =
            c.available();


        if (available > 0)
        {
            size_t remaining =
                length - received;


            size_t toRead =
                remaining;


            if ((size_t)available < toRead)
            {
                toRead =
                    available;
            }


            int n =
                c.read(
                    buffer + received,
                    toRead
                );


            if (n > 0)
            {
                received += n;

                start =
                    millis();
            }
        }
        else
        {
            if (millis() - start > timeoutMs)
            {
                return false;
            }


            delay(0);
        }
    }


    return true;
}


// ============================================================
// allocate memory
// ============================================================

bool allocateBuffers()
{
    Serial.println();

    Serial.println(
        "Allocating memory..."
    );


    // --------------------------------------------------------
    // RGB565 Framebuffer
    // --------------------------------------------------------

    size_t framebufferSize =
        DISPLAY_PIXELS *
        sizeof(uint16_t);


    committedFramebuffer =
        (uint16_t *)heap_caps_malloc(
            framebufferSize,
            MALLOC_CAP_SPIRAM |
            MALLOC_CAP_8BIT
        );


    if (committedFramebuffer == nullptr)
    {
        committedFramebuffer =
            (uint16_t *)heap_caps_malloc(
                framebufferSize,
                MALLOC_CAP_INTERNAL |
                MALLOC_CAP_8BIT
            );
    }


    if (committedFramebuffer == nullptr)
    {
        Serial.println(
            "ERROR: committedFramebuffer!"
        );

        return false;
    }


    workingFramebuffer =
        (uint16_t *)heap_caps_malloc(
            framebufferSize,
            MALLOC_CAP_SPIRAM |
            MALLOC_CAP_8BIT
        );


    if (workingFramebuffer == nullptr)
    {
        workingFramebuffer =
            (uint16_t *)heap_caps_malloc(
                framebufferSize,
                MALLOC_CAP_INTERNAL |
                MALLOC_CAP_8BIT
            );
    }


    if (workingFramebuffer == nullptr)
    {
        Serial.println(
            "FEHLER: workingFramebuffer!"
        );

        return false;
    }


    Serial.print(
        "RGB565 Framebuffer: "
    );

    Serial.print(
        framebufferSize
    );

    Serial.println(
        " Bytes je Buffer"
    );


    // --------------------------------------------------------
    // LVGL Buffer
    // --------------------------------------------------------

    size_t lvSize =
        DISPLAY_PIXELS *
        sizeof(lv_color_t);


    lvBuffer =
        (lv_color_t *)heap_caps_malloc(
            lvSize,
            MALLOC_CAP_SPIRAM |
            MALLOC_CAP_8BIT
        );


    if (lvBuffer == nullptr)
    {
        lvBuffer =
            (lv_color_t *)heap_caps_malloc(
                lvSize,
                MALLOC_CAP_INTERNAL |
                MALLOC_CAP_8BIT
            );
    }


    if (lvBuffer == nullptr)
    {
        Serial.println(
            "ERROR: lvBuffer!"
        );

        return false;
    }


    Serial.print(
        "LVGL Buffer: "
    );

    Serial.print(
        lvSize
    );

    Serial.println(
        " Bytes"
    );


    // --------------------------------------------------------
    // TCP Buffer
    // --------------------------------------------------------

    tcpPacketBuffer =
        (uint8_t *)heap_caps_malloc(
            TCP_PACKET_BUFFER_SIZE,
            MALLOC_CAP_SPIRAM |
            MALLOC_CAP_8BIT
        );


    if (tcpPacketBuffer == nullptr)
    {
        tcpPacketBuffer =
            (uint8_t *)heap_caps_malloc(
                TCP_PACKET_BUFFER_SIZE,
                MALLOC_CAP_INTERNAL |
                MALLOC_CAP_8BIT
            );
    }


    if (tcpPacketBuffer == nullptr)
    {
        Serial.println(
            "ERROR: tcpPacketBuffer!"
        );

        return false;
    }


    Serial.print(
        "TCP Packet Buffer: "
    );

    Serial.print(
        TCP_PACKET_BUFFER_SIZE
    );

    Serial.println(
        " Bytes"
    );


    // --------------------------------------------------------
    // Initialize
    // --------------------------------------------------------

    memset(
        committedFramebuffer,
        0,
        framebufferSize
    );


    memset(
        workingFramebuffer,
        0,
        framebufferSize
    );


    memset(
        lvBuffer,
        0,
        lvSize
    );


    return true;
}


// ============================================================
// prepare working frame
// ============================================================

// New frame starts with the last complete frame. Then apply PXUP/PXUR changes


void beginWorkingFrame()
{
    memcpy(
        workingFramebuffer,
        committedFramebuffer,
        DISPLAY_PIXELS *
        sizeof(uint16_t)
    );
}


// ============================================================
// RGB565 → LVGL
// ============================================================

void convertFramebufferToLVGL()
{
    uint32_t start =
        micros();


    for (int y = 0;
         y < DISPLAY_HEIGHT;
         y++)
    {
        for (int x = 0;
             x < DISPLAY_WIDTH;
             x++)
        {
            uint16_t color =
                committedFramebuffer[
                    y * DISPLAY_WIDTH + x
                ];


            uint8_t r5 =
                (color >> 11) & 0x1F;

            uint8_t g6 =
                (color >> 5) & 0x3F;

            uint8_t b5 =
                color & 0x1F;


            uint8_t r =
                (r5 << 3) |
                (r5 >> 2);


            uint8_t g =
                (g6 << 2) |
                (g6 >> 4);


            uint8_t b =
                (b5 << 3) |
                (b5 >> 2);


            lvBuffer[
                y * DISPLAY_WIDTH + x
            ] =
                lv_color_make(
                    r,
                    g,
                    b
                );
        }
    }


    lastDisplayTimeUs =
        micros() - start;
}


// ============================================================
// Update Canvas
// ============================================================

void updateCanvas()
{
    lv_canvas_set_buffer(
        canvas,
        lvBuffer,
        DISPLAY_WIDTH,
        DISPLAY_HEIGHT,
        LV_IMG_CF_TRUE_COLOR
    );


    lv_obj_invalidate(
        canvas
    );


    lv_timer_handler();
}


// ============================================================
// commit frame
// ============================================================

void commitFrame()
{
    uint32_t start =
        micros();


    // --------------------------------------------------------
    // change Buffer
    // --------------------------------------------------------

    uint16_t *temp =
        committedFramebuffer;


    committedFramebuffer =
        workingFramebuffer;


    workingFramebuffer =
        temp;


    // --------------------------------------------------------
    // prepare new working frame
    // --------------------------------------------------------

    beginWorkingFrame();


    // --------------------------------------------------------
    // RGB565 → LVGL
    // --------------------------------------------------------

    convertFramebufferToLVGL();


    // --------------------------------------------------------
    // Display
    // --------------------------------------------------------

    updateCanvas();


    lastFrameTimeUs =
        micros() - start;


    frameCounter++;
}


// ============================================================
// commit PXUP
// ============================================================

void applyPixelUpdates(
    uint16_t count
)
{
    for (uint16_t i = 0;
         i < count;
         i++)
    {
        const uint8_t *p =
            tcpPacketBuffer +
            (i * 4);


        uint8_t x =
            p[0];


        uint8_t y =
            p[1];


        uint16_t color =
            readLE16(
                p + 2
            );


        if (x < DISPLAY_WIDTH &&
            y < DISPLAY_HEIGHT)
        {
            workingFramebuffer[
                y * DISPLAY_WIDTH + x
            ] = color;
        }
    }


    pixelUpdateCounter +=
        count;
}


// ============================================================
// commit PXUR
// ============================================================

void applyRunUpdates(
    uint16_t count
)
{
    for (uint16_t i = 0;
         i < count;
         i++)
    {
        const uint8_t *p =
            tcpPacketBuffer +
            (i * 5);


        uint8_t y =
            p[0];


        uint8_t x0 =
            p[1];


        uint8_t length =
            p[2];


        uint16_t color =
            readLE16(
                p + 3
            );


        if (y >= DISPLAY_HEIGHT)
        {
            continue;
        }


        for (uint16_t x = x0;
             x < (uint16_t)x0 + length;
             x++)
        {
            if (x >= DISPLAY_WIDTH)
            {
                break;
            }


            workingFramebuffer[
                y * DISPLAY_WIDTH + x
            ] = color;
        }


        pixelUpdateCounter +=
            length;
    }
}


// ============================================================
// Packet received
// ============================================================

bool receivePacket()
{
    if (!client ||
        !client.connected())
    {
        return false;
    }


    uint32_t packetStart =
        micros();


    // --------------------------------------------------------
    // Header
    // --------------------------------------------------------

    uint8_t header[
        PACKET_HEADER_SIZE
    ];


    if (!receiveExact(
            client,
            header,
            PACKET_HEADER_SIZE,
            5000))
    {
        return false;
    }


    bytesTotal +=
        PACKET_HEADER_SIZE;


    // --------------------------------------------------------
    // read header
    // --------------------------------------------------------

    uint32_t magic =
        readLE32(
            header
        );


    uint8_t version =
        header[4];


    uint32_t frameId =
        readLE32(
            header + 5
        );


    uint16_t count =
        readLE16(
            header + 9
        );


    // --------------------------------------------------------
    // Packet type
    // --------------------------------------------------------

    bool isPixelPacket =
        (magic == PXUP_MAGIC);


    bool isRunPacket =
        (magic == PXUR_MAGIC);


    if (!isPixelPacket &&
        !isRunPacket)
    {
        Serial.print(
            "Invalid Magic Packet: 0x"
        );


        Serial.println(
            magic,
            HEX
        );


        return false;
    }


    // --------------------------------------------------------
    // Version
    // --------------------------------------------------------

    if (isPixelPacket &&
        version != PXUP_VERSION)
    {
        Serial.print(
            "Invalid PXUP version: "
        );


        Serial.println(
            version
        );


        return false;
    }


    if (isRunPacket &&
        version != PXUR_VERSION)
    {
        Serial.print(
            "Invallid PXUR version: "
        );


        Serial.println(
            version
        );


        return false;
    }


    // --------------------------------------------------------
    // Payload-Size
    // --------------------------------------------------------

    size_t itemSize =
        isPixelPacket ? 4 : 5;


    size_t payloadSize =
        (size_t)count *
        itemSize;


    if (payloadSize >
        TCP_PACKET_BUFFER_SIZE)
    {
        Serial.print(
            "Packet is too large: "
        );


        Serial.println(
            payloadSize
        );


        return false;
    }


    // --------------------------------------------------------
    // New Frame
    // --------------------------------------------------------

    if (!currentFrameValid)
    {
        currentFrameId =
            frameId;


        currentFrameValid =
            true;


        beginWorkingFrame();


        Serial.print(
            "[FRAME] Start ID="
        );


        Serial.println(
            frameId
        );
    }
    else if (frameId != currentFrameId)
    {

        // New Frame-ID. Previous frame is finished

        commitFrame();


        currentFrameId =
            frameId;


        beginWorkingFrame();
    }


    // --------------------------------------------------------
    // Payload
    // --------------------------------------------------------

    if (payloadSize > 0)
    {
        if (!receiveExact(
                client,
                tcpPacketBuffer,
                payloadSize,
                5000))
        {
            return false;
        }


        bytesTotal +=
            payloadSize;
    }


    // --------------------------------------------------------
    // commit pixel updates
    // --------------------------------------------------------

    if (isPixelPacket)
    {
        applyPixelUpdates(
            count
        );
    }
    else
    {
        applyRunUpdates(
            count
        );
    }


    packetCounter++;


    lastPacketTimeUs =
        micros() - packetStart;

    lastFramePacketRxUs =
        micros();


    return true;
}


// ============================================================
// Finish frame when TCP is on idle
// ============================================================

void finalizeFrameIfIdle()
{
    if (!currentFrameValid ||
        lastFramePacketRxUs == 0)
    {
        return;
    }

    uint32_t now =
        micros();

    if ((uint32_t)(now - lastFramePacketRxUs) >=
        FRAME_IDLE_TIMEOUT_US)
    {
        commitFrame();

        currentFrameValid =
            false;

        lastFramePacketRxUs =
            0;
    }
}


// ============================================================
// check TCP-Client
// ============================================================

void checkClient()
{
    if (client &&
        client.connected())
    {
        if (!tcpConnected)
        {
            tcpConnected =
                true;


            Serial.println(
                "TCP-Client connected."
            );
        }


        return;
    }


    if (tcpConnected)
    {
        Serial.println(
            "TCP-Client disconnected."
        );


        client.stop();


        tcpConnected =
            false;


        // discard invalid frame

        currentFrameValid =
            false;

        lastFramePacketRxUs =
            0;
    }


    WiFiClient newClient =
        server.available();


    if (newClient)
    {
        client =
            newClient;


        client.setNoDelay(
            true
        );


        tcpConnected =
            true;


        currentFrameValid =
            false;

        lastFramePacketRxUs =
            0;


        Serial.println();

        Serial.println(
            "================================"
        );


        Serial.println(
            "TCP-Client connected"
        );


        Serial.print(
            "Client IP: "
        );


        Serial.println(
            client.remoteIP()
        );


        Serial.println(
            "================================"
        );
    }
}


// ============================================================
// Statistics
// ============================================================

void printStatistics()
{
    unsigned long now =
        millis();


    unsigned long elapsed =
        now -
        statisticsStart;


    if (elapsed < 2000)
    {
        return;
    }


    float seconds =
        elapsed /
        1000.0f;


    float fps =
        frameCounter /
        seconds;


    float kbps =
        (bytesTotal /
         1024.0f) /
        seconds;


    Serial.println();


    Serial.println(
        "------------- PERFORMANCE -----------"
    );


    Serial.print(
        "Frames:        "
    );


    Serial.println(
        frameCounter
    );


    Serial.print(
        "FPS:           "
    );


    Serial.println(
        fps,
        2
    );


    Serial.print(
        "Network:       "
    );


    Serial.print(
        kbps,
        1
    );


    Serial.println(
        " KiB/s"
    );


    Serial.print(
        "Packets:       "
    );


    Serial.println(
        packetCounter
    );


    Serial.print(
        "Pixel Updates: "
    );


    Serial.println(
        pixelUpdateCounter
    );


    Serial.print(
        "RX Packet:     "
    );


    Serial.print(
        lastPacketTimeUs /
        1000.0f,
        2
    );


    Serial.println(
        " ms"
    );


    Serial.print(
        "Display:       "
    );


    Serial.print(
        lastDisplayTimeUs /
        1000.0f,
        2
    );


    Serial.println(
        " ms"
    );


    Serial.print(
        "Frame:         "
    );


    Serial.print(
        lastFrameTimeUs /
        1000.0f,
        2
    );


    Serial.println(
        " ms"
    );


    Serial.println(
        "--------------------------------------"
    );


    frameCounter =
        0;


    packetCounter =
        0;


    bytesTotal =
        0;


    pixelUpdateCounter =
        0;


    statisticsStart =
        now;
}


// ============================================================
// WLAN / WEB INTERFACE
// ============================================================

String htmlEscape(const String &input)
{
    String output = input;
    output.replace("&", "&amp;");
    output.replace("<", "&lt;");
    output.replace(">", "&gt;");
    output.replace("\"", "&quot;");
    output.replace("'", "&#39;");
    return output;
}

String wifiStatusText()
{
    if (WiFi.status() == WL_CONNECTED)
    {
        return "Connected to <b>" +
               htmlEscape(WiFi.SSID()) +
               "</b><br>STA-IP: <b>" +
               WiFi.localIP().toString() +
               "</b><br>RSSI: <b>" +
               String(WiFi.RSSI()) +
               " dBm</b>";
    }

    if (wifiConnectInProgress)
    {
        return "Attempting to connect ...";
    }

    return "Not connected to a WiFi-Hotspot";
}

String buildConfigPage()
{
    int networkCount = WiFi.scanComplete();

    // Scan synchronously if scan is not completed yet
    if (networkCount < 0)
    {
        networkCount = WiFi.scanNetworks(false, true);
    }

    String page;

    page += "<!DOCTYPE html><html><head>";
    page += "<meta charset='utf-8'>";
    page += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
    page += "<title>GLasshole WiFi configuration</title>";
    page += "<style>";
    page += "body{font-family:Arial,sans-serif;background:#111;color:#eee;margin:0;padding:20px;}";
    page += ".box{max-width:520px;margin:auto;background:#222;padding:24px;border-radius:12px;}";
    page += "h1{font-size:24px;margin-top:0;}";
    page += "label{display:block;margin-top:18px;margin-bottom:6px;}";
    page += "select,input{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border-radius:6px;border:1px solid #555;background:#333;color:#fff;}";
    page += "button{width:100%;margin-top:22px;padding:13px;font-size:17px;border:0;border-radius:6px;background:#fff;color:#111;}";
    page += ".status{background:#181818;padding:12px;border-radius:6px;line-height:1.5;}";
    page += ".hint{color:#aaa;font-size:13px;margin-top:6px;}";
    page += "</style></head><body><div class='box'>";

    page += "<h1>GLasshole</h1>";
    page += "<div class='status'>";
    page += "<b>Access Point:</b> " + String(AP_SSID) + "<br>";
    page += "AP-IP: <b>" + WiFi.softAPIP().toString() + "</b><hr>";
    page += wifiStatusText();
    page += "</div>";

    page += "<form action='/connect' method='POST'>";

    page += "<label for='ssid'>WiFi</label>";
    page += "<select id='ssid' name='ssid'>";
    page += "<option value=''>-- select WiFi --</option>";

    if (networkCount > 0)
    {
        for (int i = 0; i < networkCount; i++)
        {
            String ssid = WiFi.SSID(i);
            String displaySSID = ssid;

            if (displaySSID.length() == 0)
            {
                displaySSID = "<hidden WiFi>";
            }

            page += "<option value='" + htmlEscape(ssid) + "'>";
            page += htmlEscape(displaySSID);
            page += " (" + String(WiFi.RSSI(i)) + " dBm)";
            page += "</option>";
        }
    }

    page += "</select>";
    page += "<div class='hint'>Found WiFi will be displayed by loading this page</div>";

    page += "<label for='hidden'>Hidden WiFi (optional)</label>";
    page += "<input id='hidden' name='hidden' type='text' autocomplete='off' placeholder='SSID of the hidden WiFi'>";
    page += "<div class='hint'>If this field is not empty the specified SSID will be used</div>";

    page += "<label for='password'>Password</label>";
    page += "<input id='password' name='password' type='password' autocomplete='off' placeholder='WiFi-Password'>";

    page += "<button type='submit'>OK</button>";
    page += "</form>";

    page += "</div></body></html>";

    if (networkCount >= 0)
    {
        WiFi.scanDelete();
    }

    return page;
}

void handleRoot()
{
    webServer.send(
        200,
        "text/html; charset=utf-8",
        buildConfigPage()
    );
}

void startWiFiConnection(const String &ssid, const String &password)
{
    configuredSSID = ssid;
    configuredPassword = password;

    if (configuredSSID.length() == 0)
    {
        wifiConnectInProgress = false;
        return;
    }

    Serial.println();
    Serial.println("======================================");
    Serial.println("Preparing WiFi connection");
    Serial.print("SSID: ");
    Serial.println(configuredSSID);
    Serial.println("======================================");

    wifiConnectInProgress = true;
    wifiConnectStartMs = millis();

    // AP keeps active until the HTTP response. Then the WiFi mode is switched
}

void handleConnect()
{
    String ssid = webServer.arg("ssid");
    String password = webServer.arg("password");
    String hiddenSSID = webServer.arg("hidden");

    hiddenSSID.trim();
    ssid.trim();

    // Hidden-SSID-Field is privileged
    if (hiddenSSID.length() > 0)
    {
        ssid = hiddenSSID;
    }

    if (ssid.length() == 0)
    {
        webServer.send(
            400,
            "text/html; charset=utf-8",
            "<html><body><h2>No SSID is selected.</h2><a href='/'>Back</a></body></html>"
        );
        return;
    }

    startWiFiConnection(ssid, password);

    String page;
    page += "<!DOCTYPE html><html><head>";
    page += "<meta charset='utf-8'>";
    page += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
    page += "<title>GLasshole</title></head><body style='font-family:Arial;padding:30px'>";
    page += "<h2>Setting up WiFi...</h2>";
    page += "<p>SSID: <b>" + htmlEscape(ssid) + "</b></p>";
    page += "<p>Access-Point will be switched of. Please connect to your specified WiFi.</p>";
    page += "</body></html>";

    webServer.send(
        200,
        "text/html; charset=utf-8",
        page
    );
}

void handleNotFound()
{
    webServer.sendHeader("Location", "/");
    webServer.send(302, "text/plain", "Redirect");
}

void startWebServer()
{
    webServer.on("/", HTTP_GET, handleRoot);
    webServer.on("/connect", HTTP_POST, handleConnect);
    webServer.onNotFound(handleNotFound);

    webServer.begin();
    webServerStarted = true;

    Serial.println("Webserver startet.");
    Serial.print("Configuration: http://");
    Serial.println(WiFi.softAPIP());
}

void startWiFiAP()
{
    Serial.println();
    Serial.println("======================================");
    Serial.println("WiFi ACCESS POINT");
    Serial.println("======================================");

    // Setting hostname
    WiFi.setHostname(HOSTNAME);

    // Provide AP on startup
    WiFi.mode(WIFI_AP);

    bool apStarted = WiFi.softAP(
        AP_SSID,
        AP_PASSWORD
    );

    if (!apStarted)
    {
        Serial.println("ERROR: Failed to start Access-Point!");
    }
    else
    {
        Serial.print("AP SSID: ");
        Serial.println(AP_SSID);

        Serial.print("AP IP:   ");
        Serial.println(WiFi.softAPIP());
    }

    // start mDNS on AP-Mode
    if (MDNS.begin(HOSTNAME))
    {
        MDNS.addService("http", "tcp", 80);
        Serial.print("mDNS reachable: http://");
        Serial.print(HOSTNAME);
        Serial.println(".local");
    }
    else
    {
        Serial.println("Failed to start mDNS.");
    }

    // Prepare first scan for WiFi
    WiFi.scanNetworks(true, true);

    startWebServer();
}

void startTcpServer()
{
    server.begin(currentTcpPort);

    Serial.println();
    Serial.println("======================================");
    Serial.print("TCP Server Port: ");
    Serial.println(currentTcpPort);
    Serial.println("TCP Server is active.");
    Serial.println("======================================");
}

void switchToConfiguredWiFi()
{
    if (configuredSSID.length() == 0)
    {
        wifiConnectInProgress = false;
        return;
    }

    Serial.println();
    Serial.println("======================================");
    Serial.println("Change from AP to WLAN");
    Serial.println("======================================");

    // HTTP-Response has been sent
    if (webServerStarted)
    {
        webServer.stop();
        webServerStarted = false;
    }

    // disconnect TCP-Server from old NIC
    server.end();

    // disable AP
    WiFi.softAPdisconnect(true);
    delay(100);

    // Station-Modus only from here
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(HOSTNAME);
    delay(100);

    WiFi.disconnect(false, false);
    delay(100);

    Serial.print("Connecting to: ");
    Serial.println(configuredSSID);

    WiFi.begin(
        configuredSSID.c_str(),
        configuredPassword.c_str()
    );

    // always keep TCP-Server online
    server.begin(currentTcpPort);

    Serial.print("TCP-Server still listening on port ");
    Serial.println(currentTcpPort);
}

void updateWiFiConnection()
{
    if (!wifiConnectInProgress)
    {
        return;
    }

    // Switch to Station Mode after HTTP response
    static bool switchStarted = false;

    if (!switchStarted)
    {
        switchStarted = true;
        switchToConfiguredWiFi();
    }

    wl_status_t status = WiFi.status();

    if (status == WL_CONNECTED)
    {
        wifiConnectInProgress = false;
        switchStarted = false;

        Serial.println();
        Serial.println("======================================");
        Serial.println("WiFi CONNECTED!");
        Serial.println("======================================");
        Serial.print("SSID: ");
        Serial.println(WiFi.SSID());
        Serial.print("STA IP: ");
        Serial.println(WiFi.localIP());

        // restart mDNS on Wi-Fi NIC
        MDNS.end();
        if (MDNS.begin(HOSTNAME))
        {
            MDNS.addService("http", "tcp", 80);
            Serial.print("mDNS reachable via: http://");
            Serial.print(HOSTNAME);
            Serial.println(".local");
        }
        else
        {
            Serial.println("Failed to start mDNS.");
        }
        Serial.print("RSSI: ");
        Serial.print(WiFi.RSSI());
        Serial.println(" dBm");
        Serial.println("Access Point: DISABLED");
        Serial.print("TCP Server Port: ");
        Serial.println(currentTcpPort);
        Serial.println("======================================");

        return;
    }

    if (millis() - wifiConnectStartMs >= WIFI_CONNECT_TIMEOUT_MS)
    {
        wifiConnectInProgress = false;
        switchStarted = false;

        Serial.println();
        Serial.println("======================================");
        Serial.println("Wi-Fi connection failed / Timeout");
        Serial.println("======================================");
        Serial.println("Access Point is OFF.");
        Serial.print("TCP Server keeps running on ");
        Serial.print(currentTcpPort);
        Serial.println(" aktiv.");
    }
}

// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(
        115200
    );


    delay(
        1000
    );


    Serial.println();
    Serial.println();


    Serial.println(
        "======================================"
    );


    Serial.println(
        "T-Glass TCP PIXEL UPDATE"
    );


    Serial.println(
        "======================================"
    );


    Serial.println();


    Serial.print(
        "Display: "
    );


    Serial.print(
        DISPLAY_WIDTH
    );


    Serial.print(
        " x "
    );


    Serial.println(
        DISPLAY_HEIGHT
    );


    Serial.print(
        "TCP Port: "
    );


    Serial.println(
        currentTcpPort
    );


    Serial.println(
        "Protokoll: PXUP / PXUR"
    );


    // --------------------------------------------------------
    // T-Glass
    // --------------------------------------------------------

    if (!amoled.begin())
    {
        Serial.println(
            "T-Glass initialisation ERROR!"
        );


        while (1)
        {
            delay(1000);
        }
    }


    Serial.println(
        "T-Glass OK"
    );


    // --------------------------------------------------------
    // Display
    // --------------------------------------------------------

    amoled.setRotation(
        0
    );


    amoled.setBrightness(
        255
    );


    // --------------------------------------------------------
    // Physical BOOT-Button
    // --------------------------------------------------------

    bootPin.init(
        BOARD_BOOT_PIN
    );

    bootPin.setEventCallback(
        button_event_callback
    );


    // --------------------------------------------------------
    // LVGL
    // --------------------------------------------------------

    beginLvglHelper(
        amoled
    );


    Serial.println(
        "LVGL OK"
    );


    // --------------------------------------------------------
    // Background
    // --------------------------------------------------------

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_black(),
        0
    );


    // --------------------------------------------------------
    // Memory
    // --------------------------------------------------------

    if (!allocateBuffers())
    {
        while (1)
        {
            delay(1000);
        }
    }


    // --------------------------------------------------------
    // Canvas
    // --------------------------------------------------------

    canvas =
        lv_canvas_create(
            lv_scr_act()
        );


    if (canvas == nullptr)
    {
        Serial.println(
            "Canvas FEHLER!"
        );


        while (1)
        {
            delay(1000);
        }
    }


    lv_canvas_set_buffer(
        canvas,
        lvBuffer,
        DISPLAY_WIDTH,
        DISPLAY_HEIGHT,
        LV_IMG_CF_TRUE_COLOR
    );


    lv_obj_set_size(
        canvas,
        DISPLAY_WIDTH,
        DISPLAY_HEIGHT
    );


    lv_obj_align(
        canvas,
        LV_ALIGN_BOTTOM_MID,
        0,
        0
    );


    // --------------------------------------------------------
    // Startimage
    // --------------------------------------------------------

    memset(
        committedFramebuffer,
        0,
        DISPLAY_PIXELS *
        sizeof(uint16_t)
    );


    memset(
        workingFramebuffer,
        0,
        DISPLAY_PIXELS *
        sizeof(uint16_t)
    );


    convertFramebufferToLVGL();


    updateCanvas();


    // --------------------------------------------------------
    // WiFi / Configuration-AP
    // --------------------------------------------------------
    startWiFiAP();

    // --------------------------------------------------------
    // TCP
    // --------------------------------------------------------

    // TCP-Server is already running in AP-Mode. Changing to Wi-Fi client mode keeps the TCP port
    startTcpServer();


    statisticsStart =
        millis();
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // --------------------------------------------------------
    // Webserver / WLAN
    // --------------------------------------------------------
    if (webServerStarted)
    {
        webServer.handleClient();
    }

    updateWiFiConnection();

    // --------------------------------------------------------
    // Physical BOOT-Button
    // --------------------------------------------------------

    bootPin.update();


    // --------------------------------------------------------
    // LVGL
    // --------------------------------------------------------

    lv_timer_handler();


    // --------------------------------------------------------
    // TCP
    // --------------------------------------------------------

    checkClient();


    // --------------------------------------------------------
    // Packet received
    // --------------------------------------------------------

    
    // All complete and available packets will be processed sequentially

    if (tcpConnected &&
        client.connected())
    {
        while (client.available() >=
               PACKET_HEADER_SIZE)
        {
            bool ok =
                receivePacket();


            if (!ok)
            {
                Serial.println(
                    "Error receiving packet."
                );


                client.stop();


                tcpConnected =
                    false;


                currentFrameValid =
                    false;

                lastFramePacketRxUs =
                    0;


                break;
            }
        }

        if (tcpConnected &&
            client.connected())
        {
            finalizeFrameIfIdle();
        }
    }


    // --------------------------------------------------------
    // Statistics
    // --------------------------------------------------------

    printStatistics();


    delay(1);
}
