(() => {
  "use strict";

  const marker = "kotMusicBootstrapStarted";
  if (sessionStorage.getItem(marker) === "1") {
    return;
  }

  const deadline = Date.now() + 120000;

  const tryStart = () => {
    const buttons = [...document.querySelectorAll("button[aria-label]")];
    const waveButton = buttons.find((candidate) => {
      const label = (candidate.getAttribute("aria-label") || "").toLowerCase();
      const isPlay = label.includes("воспроиз") || label.includes("play");
      const isWave = label.includes("волн") || label.includes("wave");
      return isPlay && isWave;
    });
    if (waveButton) {
      sessionStorage.setItem(marker, "1");
      waveButton.click();
      return;
    }

    if (Date.now() < deadline) {
      window.setTimeout(tryStart, 500);
    }
  };

  tryStart();
})();
