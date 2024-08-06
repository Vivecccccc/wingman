// Load preprocessed data
console.log("Content script is running");
let isProcessing = false;
let pageHistory = [];

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "urlMatched") {
    console.log("URL matched:", message.url);
    waitForIframeAndFetchData();
  }
});

function waitForIframeAndFetchData() {
  const iframe = document.querySelector("iframe#main");
  if (iframe) {
    if (!iframe.hasAttribute('listener')) {
      iframe.setAttribute('listener', 'true');
      iframe.addEventListener("load", onIframeLoad);
    }

    function onIframeLoad() {
      if (isProcessing) return;
      isProcessing = true;

      console.log("Iframe loaded");
      const iframeDocument = iframe.contentDocument || iframe.contentWindow.document;
      fetchData().then(() => {
        isProcessing = false;
      });
    }

    // If the iframe is already loaded, fetch data immediately
    if (iframe.contentDocument && iframe.contentDocument.readyState === 'complete') {
      onIframeLoad();
    }
  } else {
    console.error("Iframe not found");
  }
}

function tokenizeAndCount(textList) {
  const flatTokens = textList.flatMap(text => text.toLowerCase().split(/\s+/));
  const counter = {};
  for (const token of flatTokens) {
    counter[token] = (counter[token] || 0) + 1;
  }
  return counter;
}

function diceCoefficient(counter1, counter2) {
  const intersection = Object.keys(counter1).filter(token => token in counter2);
  const sum1 = Object.values(counter1).reduce((a, b) => a + b, 0);
  const sum2 = Object.values(counter2).reduce((a, b) => a + b, 0);
  return (2 * intersection.length) / (sum1 + sum2);
}

function overlapCoefficient(counter1, counter2) {
  const intersection = Object.keys(counter1).filter(token => token in counter2);
  const min = Math.min(
    Object.keys(counter1).length,
    Object.keys(counter2).length
  );
  return intersection.length / min;
}

function findBestMatch(targetTextList, textLists, similarityFunc) {
  const targetCounter = tokenizeAndCount(targetTextList);
  let bestMatch = null;
  let maxSimilarity = 0;

  textLists.forEach((textList, index) => {
    const textCounter = tokenizeAndCount(textList);
    const similarity = similarityFunc(targetCounter, textCounter);
    if (similarity > maxSimilarity) {
      maxSimilarity = similarity;
      bestMatch = index;
    }
  });

  return { bestMatchIndex: bestMatch, maxSimilarity };
}

function findSignificantElements(root = document.body) {
  const significantElements = [];

  function isSignificantText(text) {
    text = text.trim();
    return text.length > 0 && text.length < 100 && !/^\d+$/.test(text);
  }

  function getClassNames(element) {
    if (typeof element.className === "string") {
      return element.className
        .split(" ")
        .filter((name) => name.length > 0);
    } else if (element.className && element.className.baseVal) {
      return element.className.baseVal
        .split(" ")
        .filter((name) => name.length > 0);
    } else if (element.classList && element.classList.length > 0) {
      return Array.from(element.classList);
    }
    return [];
  }

  function escapeSelector(selector) {
    return selector.replace(/[!"#$%&'()*+,./:;<=>?@[\]^`{|}~]/g, "\\$&");
  }

  function getElementSelector(element) {
    if (element.id) {
      return `#${escapeSelector(element.id)}`;
    }
    let selector = element.tagName.toLowerCase();
    const classNames = getClassNames(element);
    if (classNames.length > 0) {
      selector += `.${escapeSelector(classNames[0])}`;
    }
    return selector;
  }

  function getAllAncestors(element) {
    const ancestors = [];
    let current = element;
    let level = 0;

    while (current && current !== root) {
      ancestors.unshift({
        selector: getElementSelector(current),
        level: level,
        element: current.tagName.toLowerCase(),
      });
      current = current.parentElement;
      level++;
    }

    // Add the root element
    ancestors.unshift({
      selector: getElementSelector(root),
      level: level,
      element: root.tagName.toLowerCase(),
    });

    return ancestors;
  }

  function findUniqueSelector(ancestors) {
    for (let i = ancestors.length - 1; i >= 0; i--) {
      const selector = ancestors
        .slice(i)
        .map((a) => a.selector)
        .join(" > ");
      if (document.querySelectorAll(selector).length === 1) {
        return selector;
      }
    }
    return ancestors.map((a) => a.selector).join(" > ");
  }

  function shouldSkipElement(element) {
    const skipTags = ["script", "style", "noscript", "iframe", "svg"];
    return skipTags.includes(element.tagName.toLowerCase());
  }

  function isInteractiveElement(element) {
    const interactiveTags = [
      "a",
      "button",
      "input",
      "select",
      "textarea",
      "option",
    ];
    if (interactiveTags.includes(element.tagName.toLowerCase())) {
      return true;
    }
    if (element.onclick || element.getAttribute("onclick")) {
      return true;
    }
    if (
      element.tagName.toLowerCase() === "a" &&
      element.getAttribute("href") &&
      element.getAttribute("href").startsWith("javascript:")
    ) {
      return true;
    }
    return element.hasAttribute("tabindex");
  }

  function getElementInfo(element) {
    const info = {
      element: element.tagName.toLowerCase(),
      type: element.type || "text",
      isInteractive: isInteractiveElement(element),
    };

    if (
      element.tagName.toLowerCase() === "a" ||
      element.tagName.toLowerCase() === "area"
    ) {
      const href = element.getAttribute("href");
      if (href) {
        info.href = href;
      }
    }

    return info;
  }

  function traverse(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (isSignificantText(node.textContent)) {
        processSignificantText(node.textContent, node.parentElement);
      }
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      if (shouldSkipElement(node)) {
        return; // Skip this element and its children
      }

      // Check for input elements with type "button" or "submit"
      if (
        node.tagName.toLowerCase() === "input" &&
        (node.type === "button" || node.type === "submit")
      ) {
        if (isSignificantText(node.value)) {
          processSignificantText(node.value, node);
        }
      }
      // Check for button elements
      else if (node.tagName.toLowerCase() === "button") {
        if (isSignificantText(node.textContent)) {
          processSignificantText(node.textContent, node);
        }
      }

      for (let child of node.childNodes) {
        traverse(child);
      }
    }
  }

  function processSignificantText(text, element) {
    const ancestors = getAllAncestors(element);
    const uniqueSelector = findUniqueSelector(ancestors);
    significantElements.push({
      text: text.trim(),
      ancestors: ancestors,
      uniqueSelector: uniqueSelector,
      ...getElementInfo(element),
    });
  }

  traverse(root);
  return significantElements;
}

function tokenizeAndCount(textList) {
  const flatTokens = textList.flatMap(text => text.toLowerCase().split(/\s+/));
  const counter = {};
  for (const token of flatTokens) {
    counter[token] = (counter[token] || 0) + 1;
  }
  return counter;
}

function diceCoefficient(counter1, counter2) {
  const intersection = Object.keys(counter1).filter(token => token in counter2);
  const sum1 = Object.values(counter1).reduce((a, b) => a + b, 0);
  const sum2 = Object.values(counter2).reduce((a, b) => a + b, 0);
  return (2 * intersection.length) / (sum1 + sum2);
}

function overlapCoefficient(counter1, counter2) {
  const intersection = Object.keys(counter1).filter(token => token in counter2);
  const min = Math.min(
    Object.keys(counter1).length,
    Object.keys(counter2).length
  );
  return intersection.length / min;
}

function findBestMatch(targetTextList, textDict, similarityFunc) {
  const targetCounter = tokenizeAndCount(targetTextList);
  let bestMatchKey = null;
  let maxSimilarity = 0;

  Object.entries(textDict).forEach(([key, textList]) => {
    const textCounter = tokenizeAndCount(textList);
    const similarity = similarityFunc(targetCounter, textCounter);
    if (similarity > maxSimilarity) {
      maxSimilarity = similarity;
      bestMatchKey = key;
    }
  });

  return { bestMatchKey, maxSimilarity };
}

function fetchData() {
  return new Promise((resolve, reject) => {
  fetch(chrome.runtime.getURL("preprocessed_data.json"))
    .then((response) => {
      if (!response.ok) {
        throw new Error(`HTTP error! Status: ${response.status}`);
      }
      return response.json();
    })
    .then((preprocessedData) => {
      let iframeRoot = document
        .querySelector("iframe#main")
        .contentDocument.querySelector("html");
      let iframeElements = findSignificantElements(iframeRoot);

      function extractSafeInfo(elements) {
        return Array.from(elements.map((el) => el.text));
      }

      const iframeTexts = extractSafeInfo(iframeElements);

      const resultOverlapCoef = findBestMatch(iframeTexts, preprocessedData, overlapCoefficient);
      console.log(
        `Best match page (Overlap coefficient): ${
          resultOverlapCoef.bestMatchKey
        }, Similarity: ${resultOverlapCoef.maxSimilarity.toFixed(4)}`
      );

      if (pageHistory.length === 0 || pageHistory[pageHistory.length - 1] !== resultOverlapCoef.bestMatchKey) {
        pageHistory.push(resultOverlapCoef.bestMatchKey);
      }

      // Send the result back to the popup
      chrome.runtime.sendMessage({ 
        action: "pageUpdated",
        currentPage: resultOverlapCoef.bestMatchKey,
        pageHistory: pageHistory
      });
      resolve();
    })
    .catch((error) => {
      console.error("Error loading preprocessed data:", error);
      chrome.runtime.sendMessage({ error: "Error loading preprocessed data." });
      reject(error);
    });
  });
}

function injectHighlightStyles(documentScope) {
  if (!documentScope.querySelector("style.highlighted-style")) {
    const style = documentScope.createElement("style");
    style.classList.add("highlighted-style");
    style.textContent = `
      .highlighted {
        position: relative;
        background-color: rgba(0, 85, 255, 0.25) !important;
        z-index: 9999;
      }
    `;
    documentScope.head.appendChild(style);
  }
}

function injectCSSIntoAllFrames() {
  injectHighlightStyles(document);
  const iframe = document.querySelector('iframe#main')
  if (iframe) {
    try {
      const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
      injectHighlightStyles(iframeDoc);
    } catch (e) {
      console.error('Unable to access iframe:', e);
    }
  }
}

injectCSSIntoAllFrames();

function selectorTwister(selector) {
  let rootNode;
  let twistedSelector
  if (selector.startsWith("iframe#main")) {
    rootNode = document.querySelector("iframe#main").contentDocument;
    parts = selector.split(" > ");
    parts.shift();
    twistedSelector = parts.join(" > ");
  } else if (selector.startsWith(".header-placeholder")) {
    rootNode = document;
    twistedSelector = selector
  } else if (selector.startsWith("footer.container")) {
    rootNode = document;
    twistedSelector = selector
  }
  return { rootNode, twistedSelector };
}

function isChildOfNav(element) {
  let current = element;
  while (current) {
    if (current && current.id && current.id === "navigation-bar") {
      return true;
    }
    current = current.parentElement;
  }
  return false;
}

function isChildOfTopNav(element) {
  let current = element;
  while (current) {
    if (current && current.classList && current.classList.contains("header-navigation")) {
      return true;
    }
    current = current.parentElement;
  }
  return false;
}

function highlightElements(selectors, texts) {
  selectors.forEach((selector, index) => {
    const { rootNode, twistedSelector } = selectorTwister(selector);
    injectHighlightStyles(rootNode);
    const elements = rootNode.querySelectorAll(twistedSelector);
    elements.forEach(element => {
      if (element.textContent.trim() === texts[index].trim()) {
        element.classList.add("highlighted");
        if (isChildOfNav(element)) {
          // find the nearest parent that has an ID
          let current = element;
          while (current && (!current.id || current.id === "")) {
            current = current.parentElement;
          }
          if (current) {
            current.classList.add("highlighted");
          }
        } else if (isChildOfTopNav(element)) {
          let current = element;
          while (current && (!current.classList || !current.classList.contains("submenulist"))) {
            current = current.parentElement;
          }
          if (current) {
            current.classList.add("highlighted");
          }
        }
      }
    });
  });
}

function highlightPage(selector) {
  injectHighlightStyles(document);
  const element = document.querySelector(selector);
  if (element) {
    element.classList.add("highlighted");
  }
}

function clearHighlights() {
  const highlightedElements = document.querySelectorAll(".highlighted");
  highlightedElements.forEach(element => {
    element.classList.remove("highlighted");
  });

  document.querySelectorAll('iframe').forEach(iframe => {
    try {
      const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
      const iframeElements = iframeDoc.querySelectorAll(".highlighted");
      iframeElements.forEach(element => {
        element.classList.remove("highlighted");
      });
    } catch (e) {
      console.error('Unable to access iframe:', e);
    }
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "getLatestPage") {
    fetchData().then(() => {
      sendResponse({ success: true });
    }).catch((error) => {
      sendResponse({ success: false, error: error.message });
    });
    return true; // Indicates that the response is sent asynchronously
  } else if (message.action === "highlightElements") {
    highlightElements(message.selectors, message.texts);
  } else if (message.action === "clearHighlights") {
    clearHighlights();
  } else if (message.action === "highlightPage") {
    highlightPage(message.selector);
  }
});