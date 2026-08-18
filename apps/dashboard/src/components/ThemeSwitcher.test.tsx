import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../services/theme-context";
import { ThemeSwitcher } from "./ThemeSwitcher";

let systemThemeListener: ((event: MediaQueryListEvent) => void) | undefined;

function useSystemDarkMode(matches: boolean): void {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
    matches,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => {
      systemThemeListener = listener;
    },
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.removeProperty("color-scheme");
  systemThemeListener = undefined;
  vi.unstubAllGlobals();
});

describe("theme switcher", () => {
  it("defaults to the system theme and follows system changes", () => {
    useSystemDarkMode(false);
    render(<ThemeProvider><ThemeSwitcher /></ThemeProvider>);

    expect(screen.getByRole("button", { name: "System" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");

    act(() => systemThemeListener?.({ matches: true } as MediaQueryListEvent));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("persists an explicit light or dark preference", () => {
    useSystemDarkMode(false);
    render(<ThemeProvider><ThemeSwitcher /></ThemeProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("project-emerge-theme")).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("project-emerge-theme")).toBe("light");
  });
});
