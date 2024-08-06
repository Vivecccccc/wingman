let currentPageHistory = [];

chrome.webRequest.onCompleted.addListener(
  function(details) {
    if (details.url === 'https://internet-banking.hk.dbs.com/IB/Welcome') {
      console.log('DBS Internet Banking page loaded');
      chrome.tabs.sendMessage(details.tabId, { action: "urlMatched", url: details.url });
    }
  },
  { urls: ['<all_urls>'] }
);

let ws;
let pendingResponses = {}
let endpoint = 'ws://47.83.26.73:8000/navigate_ws';
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "pageUpdated") {
    currentPageHistory = message.pageHistory;
  } else if (message.action === "sendToBackend") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      ws = new WebSocket(endpoint);

      ws.onopen = () => {
        sendWebSocketMessage(message.query, sendResponse);
      };

      ws.onmessage = (event) => {
        const data = event.data;
        try {
          const parsedData = JSON.parse(data);
          // Check if this is a final response or an intermediate result
          if (parsedData.type === 'final' && pendingResponses[parsedData.id]) {
            pendingResponses[parsedData.id]({success: true, result: parsedData});
            delete pendingResponses[parsedData.id];
          } else if (parsedData.type.startsWith('stage_')) {
            // Send intermediate result to popup
            chrome.runtime.sendMessage({
              action: "intermediateResult",
              result: parsedData
            });
          }
        } catch (e) {
          console.error("Error parsing WebSocket message:", e);
        }
      };

      ws.onerror = (error) => {
        sendResponse({ success: false, error: error.message });
      };

      ws.onclose = () => {
        console.log('WebSocket connection closed');
        if (pendingResponses) {
          for (const id in pendingResponses) {
            pendingResponses[id]({success: false, error: 'Fail to establish connection right now, please try again later'});
          }
          pendingResponses = {};
        }
      };
    } else {
      sendWebSocketMessage(message.query, sendResponse);
    }

    return true; // Indicates that the response is sent asynchronously
  }
});

function sendWebSocketMessage(query, sendResponse) {
  const id = Date.now(); // Use a unique ID for each message
  pendingResponses[id] = sendResponse;

  let body = JSON.stringify({
    id: id,
    user_query: query,
    page_history: currentPageHistory
  });

  ws.send(body);
}