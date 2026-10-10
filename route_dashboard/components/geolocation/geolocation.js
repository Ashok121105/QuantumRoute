(() => {
  const button = document.getElementById("location");
  const status = document.getElementById("status");
  const send = (value) => window.parent.postMessage({
    isStreamlitMessage: true,
    type: "streamlit:setComponentValue",
    value
  }, "*");

  window.parent.postMessage({
    isStreamlitMessage: true,
    type: "streamlit:componentReady",
    apiVersion: 1
  }, "*");
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "streamlit:render") {
      window.parent.postMessage({
        isStreamlitMessage: true,
        type: "streamlit:setFrameHeight",
        height: 56
      }, "*");
    }
  });

  button.addEventListener("click", () => {
    if (!navigator.geolocation) {
      status.textContent = "Browser geolocation is unavailable. Use manual place search.";
      send({error: "Browser geolocation is unavailable. Use manual place search."});
      return;
    }
    button.disabled = true;
    status.textContent = "Waiting for browser location permission…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const {latitude, longitude, accuracy} = position.coords;
        status.textContent = "Location received with your permission.";
        send({latitude, longitude, accuracy, capturedAt: new Date().toISOString()});
        button.disabled = false;
      },
      (error) => {
        const message = error.code === error.PERMISSION_DENIED
          ? "Location permission was denied. Manual place search is still available."
          : error.code === error.POSITION_UNAVAILABLE
            ? "Your browser could not determine a location. Manual place search is still available."
            : "Location request timed out. Try again or use manual place search.";
        status.textContent = message;
        send({error: message});
        button.disabled = false;
      },
      {enableHighAccuracy: true, timeout: 15000, maximumAge: 0}
    );
  });
})();
