document.addEventListener("DOMContentLoaded", function () {
  const queryInput = document.getElementById("query");
  const submitButton = document.getElementById("submit");
  const resultSection = document.getElementById("result-section");
  const resultContainer = document.getElementById("result");
  const intermediateSection = document.getElementById("intermediate-section");
  const intermediateResultDiv = document.getElementById("intermediate-result");
  const progressRing = document.getElementById("progress-ring");

  submitButton.addEventListener("click", function () {
    const query = queryInput.value;
    submitButton.disabled = true;
    submitButton.textContent = "Processing...";

    // Reset previous results
    intermediateSection.style.display = "none";
    resultSection.style.display = "none";
    intermediateResultDiv.innerHTML = "";
    resultContainer.innerHTML = "";
    progressRing.style.display = "none";

    // Request the latest page from the content script
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      chrome.tabs.sendMessage(
        tabs[0].id,
        { action: "getLatestPage" },
        function (response) {
          if (response.success) {
            // Now send the query and page history to the backend
            chrome.runtime.sendMessage(
              {
                action: "sendToBackend",
                query: query,
              },
              function (backendResponse) {
                if (backendResponse.success) {
                  // Display the result from the backend
                  progressRing.style.display = "none";
                  resultSection.style.display = "block";
                  backendResponse.result.linkages.forEach((item) => {
                    const instruction = item['instruction'];
                    const portalSelector = item["portal"]["selector"]
                    const portalText = item["portal"]["text"];
                    const selectors = [portalSelector];
                    const texts = [portalText];
                    const resultElement = document.createElement("div");
                    resultElement.className = "result-item";
                    resultElement.textContent = instruction;
                    resultElement.dataset.selectors = JSON.stringify(selectors);
                    resultElement.dataset.texts = JSON.stringify(texts);
                    resultContainer.appendChild(resultElement);
                  });
                  // backendResponse.result.relevances.forEach((item) => {
                  //   const elementText = item['text']
                  //   const elementSelector = item['selector']
                  //   const elementHint = item['hint']
                  //   // put them in the last child of resultContainer
                  //   const lastResultElement = resultContainer.lastChild;
                  //   const lastResultElementSelector = JSON.parse(lastResultElement.dataset.selectors);
                  //   const lastResultElementText = JSON.parse(lastResultElement.dataset.texts);
                  //   lastResultElementSelector.push(elementSelector);
                  //   lastResultElementText.push(elementText);
                  //   lastResultElement.dataset.selectors = JSON.stringify(lastResultElementSelector);
                  //   lastResultElement.dataset.texts = JSON.stringify(lastResultElementText);
                  // })
                } else {
                  resultSection.style.display = "block";
                  progressRing.style.display = "none";
                  resultContainer.textContent =
                    "Error: " + backendResponse.error;
                }
                submitButton.disabled = false;
                submitButton.textContent = "Submit";
              }
            );
          } else {
            resultSection.style.display = "block";
            progressRing.style.display = "none";
            resultContainer.textContent = "Error: " + response.error;
            submitButton.disabled = false;
            submitButton.textContent = "Submit";
          }
        }
      );
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message.action === "intermediateResult") {
      if (message.result.type === "stage_1") {
        intermediateResultDiv.innerHTML = "";
        intermediateSection.style.display = "block";
        if (Array.isArray(message.result['relevant_pages'])) {
          const relevantPages = message.result['relevant_pages'].filter((tag) => tag !== "Header of the page" && tag !== "Footer of the page");
          relevantPages.forEach((tag) => {
            const tagElement = document.createElement("span");
            tagElement.className = "tag";
            tagElement.textContent = tag;
            intermediateResultDiv.appendChild(tagElement);
          });
        } else {
          const itemDiv = document.createElement("div");
          itemDiv.textContent = JSON.stringify(message.result);
          intermediateResultDiv.appendChild(itemDiv);
        }
      } else if (message.result.type === "stage_2") {
        // tags from stage_1 should be highlighted based on the updated result from stage_2
        const tags = document.querySelectorAll(".tag");
        const relevantPages = message.result['relevant_pages'];
        tags.forEach((tag) => {
          if (relevantPages.includes(tag.textContent)) {
            tag.style.backgroundColor = "red";
            tag.style.color = "white";
          }
        });
      }
      resultSection.style.display = "block";
      progressRing.style.display = "block";
      resultContainer.innerHTML = "";
    }
  });

  document.addEventListener("mouseover", function (event) {
    if (event.target.matches(".result-item")) {
      const selectors = JSON.parse(event.target.dataset.selectors);
      const texts = JSON.parse(event.target.dataset.texts);
      chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
        chrome.tabs.sendMessage(tabs[0].id, { action: "highlightElements", selectors: selectors, texts: texts });
      });
    } else if (event.target.matches(".tag")) {
      // split textContent by " - "
      const parent = event.target.textContent.split(" - ")[0];
      const selectorMapping = {};
      selectorMapping["Footer of the page"] = "footer.container";
      selectorMapping["Header of the page"] = ".header-placeholder";
      selectorMapping["Cards"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #card";
      selectorMapping["Invest"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #invest"
      selectorMapping["My Accounts"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #accounts"
      selectorMapping["Open"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #apply"
      selectorMapping["Pay"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #pay"
      selectorMapping["Transfer"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #transfer"
      selectorMapping["Preferences"] = ".header-placeholder > .navbar > .navbar-inner > .header-navigation > .header-menu > :nth-child(5)"
      selectorMapping["Messages"] = ".header-placeholder > .navbar > .navbar-inner > .header-navigation > .header-menu > :nth-child(4)"
      selectorMapping["Request"] = ".header-placeholder > #navigation-bar > div > ul > .submenulist > #request"
      const selector = selectorMapping[parent];
      chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
        chrome.tabs.sendMessage(tabs[0].id, { action: "highlightPage", selector: selector });
      });
    }
  });

  document.addEventListener("mouseout", function (event) {
    if (event.target.matches(".result-item") || event.target.matches(".tag")) {
      chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
        chrome.tabs.sendMessage(tabs[0].id, { action: "clearHighlights" });
      });
    }
  });
});
