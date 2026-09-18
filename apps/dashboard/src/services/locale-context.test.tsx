import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LocaleProvider, useLocale } from "./locale-context";
import { LanguageSwitcher } from "../components/LanguageSwitcher";

function TestConsumer(): React.JSX.Element {
  const { locale, setLocale, t } = useLocale();
  return (
    <div>
      <span data-testid="current-locale">{locale}</span>
      <span data-testid="status-connected">{t.status.connected}</span>
      <button type="button" onClick={() => setLocale(locale === "it" ? "en" : "it")}>
        Toggle
      </button>
    </div>
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute("lang");
});

describe("locale context and language switcher", () => {
  it("defaults to Italian (it) and updates document language attribute", () => {
    render(
      <LocaleProvider>
        <TestConsumer />
      </LocaleProvider>,
    );

    expect(screen.getByTestId("current-locale").textContent).toBe("it");
    expect(screen.getByTestId("status-connected").textContent).toBe("MQTT connesso");
    expect(document.documentElement.getAttribute("lang")).toBe("it");
  });

  it("switches language and persists preference to localStorage", () => {
    render(
      <LocaleProvider>
        <TestConsumer />
      </LocaleProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Toggle" }));

    expect(screen.getByTestId("current-locale").textContent).toBe("en");
    expect(screen.getByTestId("status-connected").textContent).toBe("MQTT connected");
    expect(document.documentElement.getAttribute("lang")).toBe("en");
    expect(window.localStorage.getItem("project-emerge-locale")).toBe("en");
  });

  it("restores saved locale from localStorage", () => {
    window.localStorage.setItem("project-emerge-locale", "en");

    render(
      <LocaleProvider>
        <TestConsumer />
      </LocaleProvider>,
    );

    expect(screen.getByTestId("current-locale").textContent).toBe("en");
    expect(screen.getByTestId("status-connected").textContent).toBe("MQTT connected");
    expect(document.documentElement.getAttribute("lang")).toBe("en");
  });

  it("provides fallback Italian values outside LocaleProvider", () => {
    render(<TestConsumer />);

    expect(screen.getByTestId("current-locale").textContent).toBe("it");
    expect(screen.getByTestId("status-connected").textContent).toBe("MQTT connesso");
  });

  it("works with LanguageSwitcher component", () => {
    render(
      <LocaleProvider>
        <LanguageSwitcher />
      </LocaleProvider>,
    );

    const itButton = screen.getByRole("button", { name: "IT" });
    const enButton = screen.getByRole("button", { name: "EN" });

    expect(itButton).toHaveAttribute("aria-pressed", "true");
    expect(enButton).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(enButton);

    expect(itButton).toHaveAttribute("aria-pressed", "false");
    expect(enButton).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.getAttribute("lang")).toBe("en");
    expect(window.localStorage.getItem("project-emerge-locale")).toBe("en");
  });
});
