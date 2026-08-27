(() => {
  "use strict";

  const marker = "kotMusicBootstrapStarted";
  if (sessionStorage.getItem(marker) === "1") {
    return;
  }

  const playLabels = ["Playback", "Воспроизведение", "Play"];
  const deadline = Date.now() + 30000;

  const tryStart = () => {
    const player =
      document.querySelector('[aria-labelledby="player-region"]') ||
      document.querySelector('[aria-label="Player"]') ||
      document.querySelector('[aria-label="Плеер"]') ||
      document;

    const button = playLabels
      .map((label) => player.querySelector(`button[aria-label="${label}"]`))
      .find(Boolean);

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
