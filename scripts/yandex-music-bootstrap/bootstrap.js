(() => {
  "use strict";

  const marker = "kotMusicBootstrapStarted";
  if (sessionStorage.getItem(marker) === "1") {
    return;
  }

  const deadline = Date.now() + 30000;

  const tryStart = () => {
    const player =
      document.querySelector('[aria-labelledby="player-region"]') ||
      document.querySelector('[aria-label="Player"]') ||
      document.querySelector('[aria-label="Плеер"]') ||
      document;

    const buttons = [...document.querySelectorAll("button[aria-label]")];
    const waveButton = buttons.find((candidate) => {
      const label = (candidate.getAttribute("aria-label") || "").toLowerCase();
      const isPlay = label.includes("воспроиз") || label.includes("play");
      const isWave = label.includes("волн") || label.includes("wave");
      return isPlay && isWave;
    });
    const playerButton = ["Playback", "Воспроизведение", "Play"]
      .map((label) => player.querySelector(`button[aria-label="${label}"]`))
      .find(Boolean);
    const button = waveButton || playerButton;

    if (button) {
      sessionStorage.setItem(marker, "1");
      button.click();
      return;
    }

    if (Date.now() < deadline) {
      window.setTimeout(tryStart, 500);
    }
  };

  tryStart();
})();
