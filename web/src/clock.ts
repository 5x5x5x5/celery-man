import { useEffect, useState } from "react";

let audio: HTMLAudioElement | null = null;
let stopWatch: (() => void) | null = null;
const song = () => (audio ??= new Audio());

/** Points the one shared audio element at the song. */
export function setSong(url: string): void {
  const a = song();
  if (!a.src.endsWith(url)) a.src = url;
}

/** Index of the frame at song time t in a window starting at `start`; never before the first. */
export const frameIndex = (t: number, start: number, fps: number): number => Math.max(0, Math.floor((t - start) * fps));

/** Plays [start, end), stopping at the end or jumping back to start when `loop`. */
export function playWindow(start: number, end: number, loop: boolean): void {
  const a = song();
  stopWatch?.();
  a.currentTime = start;
  let raf = 0;
  const watch = () => {
    if (a.currentTime >= end) {
      if (loop) a.currentTime = start;
      else return void a.pause();
    }
    raf = requestAnimationFrame(watch);
  };
  stopWatch = () => cancelAnimationFrame(raf);
  void a.play()?.catch(() => {});
  raf = requestAnimationFrame(watch);
}

export const seek = (t: number): void => {
  song().currentTime = t;
};

export const isPlaying = (): boolean => !song().paused;

export const pause = (): void => {
  stopWatch?.();
  song().pause();
};

/** The song's current time in seconds: every animation frame while playing, on seek/pause otherwise. */
export function useSongTime(): number {
  const [t, setT] = useState(() => song().currentTime);
  useEffect(() => {
    const a = song();
    let raf = 0;
    const tick = () => {
      setT(a.currentTime);
      if (!a.paused) raf = requestAnimationFrame(tick);
    };
    const kick = () => {
      cancelAnimationFrame(raf);
      tick();
    };
    const events = ["play", "pause", "seeked"];
    events.forEach((e) => a.addEventListener(e, kick));
    kick();
    return () => {
      cancelAnimationFrame(raf);
      events.forEach((e) => a.removeEventListener(e, kick));
    };
  }, []);
  return t;
}
