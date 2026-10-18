"""Synthetic-only Edge 5 browser acceptance against a running local service.

Exercises real network jobs, multi-selection, deterministic evidence, Plotly
inspection/measurement, and reviewed deliverables. Never imports CAD assets.
"""
import json
import math
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.environ.get("PIPEFORGE_UI_BASE_URL", "http://127.0.0.1:8000")
    artifacts = Path(os.environ.get("PIPEFORGE_UI_ARTIFACTS", "/workspace/pipeforge-artifacts/edge-5-ui"))
    artifacts.mkdir(parents=True, exist_ok=True)
    evidence = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/connected", wait_until="networkidle")
        expect(page.locator("#network-summary")).to_contain_text("nodes")
        assert page.locator("#example option").count() >= 16

        def load(identifier):
            page.select_option("#example", identifier)
            page.click("#load-example")
            expect(page.locator("#network-json")).to_have_value(__import__("re").compile(identifier))
            expect(page.locator("#optimize")).to_be_enabled()
            assert json.loads(page.input_value("#network-json"))["provenance"] == "synthetic"

        def calculate():
            with page.expect_response(lambda response: response.url.endswith("/api/network/jobs")) as submitted:
                page.click("#optimize")
            request = submitted.value.request.post_data_json
            expect(page.locator("#progress")).to_contain_text("Completed", timeout=150000)
            expect(page.locator("#error")).to_be_hidden()
            return request, page.evaluate("response.result")

        load("09-coordinated-improvement")
        page.select_option("#selection-scope", "spans")
        page.get_by_label("Optimize span firemain", exact=True).uncheck()
        page.select_option("#strategy", "length")
        request, result = calculate()
        assert request["selected_span_ids"] == ["supply"]
        assert result["selection"]["selected_span_ids"] == ["supply"]
        assert result["status"] == "NO IMPROVEMENT FOUND"
        original_fire = next(s for s in result["original_network"]["spans"] if s["id"] == "firemain")
        resulting_fire = next(s for s in (result["proposed_network"] or result["original_network"])["spans"] if s["id"] == "firemain")
        assert original_fire == resulting_fire
        evidence["selected_span_preserves_other_system"] = {"status": result["status"], "selected": request["selected_span_ids"]}

        page.select_option("#selection-scope", "systems")
        expect(page.get_by_label("Optimize system cooling", exact=True)).to_be_visible()
        expect(page.get_by_label("Optimize system firemain", exact=True)).to_be_checked()
        page.get_by_label("Optimize system firemain", exact=True).uncheck()
        request, result = calculate()
        assert request["selected_system_ids"] == ["cooling"]
        assert result["selection"]["selected_system_ids"] == ["cooling"]
        evidence["system_selection"] = request["selected_system_ids"]

        page.select_option("#selection-scope", "all")
        page.select_option("#strategy", "balanced")
        request, result = calculate()
        assert "selected_span_ids" not in request and "selected_system_ids" not in request
        assert result["status"] == "IMPROVED", result
        assert set(result["changed_span_ids"]) == {"supply", "firemain"}
        assert result["validation"]["hard_failures"] == 0
        assert result["after"]["length_mm"] < result["before"]["length_mm"]
        expect(page.locator("#copilot")).to_contain_text("2 spans change together across 2 systems")
        expect(page.locator("#copilot")).to_contain_text("UNVERIFIED objective terms: hydraulic")
        page.get_by_text("Evaluated network candidates and tradeoffs", exact=True).click()
        assert page.locator("#candidates table tr").count() >= 2
        evidence["coordinated_whole_network"] = {"status": result["status"], "before": result["before"], "after": result["after"], "changed": result["changed_span_ids"], "hard_failures": result["validation"]["hard_failures"]}

        page.select_option("#isolate-system", "firemain")
        page.wait_for_function("document.getElementById('network-plot').data.filter(t=>t.customdata?.[0]?.span).every(t=>t.customdata[0].span.system_id==='firemain')")
        page.select_option("#comparison-mode", "side-by-side")
        expect(page.locator("#proposal-pane")).to_be_visible()
        page.wait_for_function("document.getElementById('network-plot-proposed').data?.some(t=>t.customdata?.[0]?.span)")
        page.get_by_role("button", name="Top · XY", exact=True).click()
        assert page.evaluate("document.getElementById('network-plot').layout.scene.camera.eye.z") == 2.2
        page.locator("#context-opacity").fill("35")
        page.locator("#context-opacity").dispatch_event("input")
        expect(page.locator("#opacity-value")).to_have_text("35%")
        page.select_option("#isolate-system", "all")
        page.select_option("#comparison-mode", "overlay")
        page.get_by_role("button", name="Isometric", exact=True).click()
        page.uncheck("#show-unchanged")
        page.check("#show-unchanged")
        page.get_by_text("Network topology and span inspection", exact=True).click()
        page.get_by_role("button", name="Inspect span supply", exact=True).click()
        expect(page.locator("#selection")).to_contain_text("Selected pipe span · supply")
        expect(page.locator("#selection")).to_contain_text("CAD handle")

        # Dispatch real Plotly point-pick payloads using coordinates from its
        # rendered trace. Verify the tool's Euclidean result independently.
        page.click("#measure-start")
        picked = page.evaluate("""() => {
          const plot=document.getElementById('network-plot');
          const trace=plot.data.find(t=>t.type==='scatter3d'&&t.customdata?.[0]?.span);
          const points=[0,trace.x.length-1].map(i=>({x:trace.x[i],y:trace.y[i],z:trace.z[i],customdata:trace.customdata[i]}));
          for(const point of points)plot.emit('plotly_click',{points:[point]});
          return points.map(p=>[p.x,p.y,p.z]);
        }""")
        expected_distance = math.dist(*picked)
        assert expected_distance > 0
        expect(page.locator("#measurement")).to_contain_text("Point-to-point distance")
        assert abs(page.evaluate("Math.hypot(...measurementPoints[1].map((v,i)=>v-measurementPoints[0][i]))") - expected_distance) < 1e-9
        evidence["inspection_and_measurement"] = {"picked_wcs_mm": picked, "distance_mm": expected_distance, "camera_presets": True, "system_isolation": True, "side_by_side": True}

        expect(page.locator("#export")).to_be_disabled()
        page.check("#approve")
        page.click("#export")
        expect(page.locator("#downloads a")).to_have_count(8, timeout=30000)
        urls = page.locator("#downloads a").evaluate_all("nodes=>nodes.map(n=>n.getAttribute('href'))")
        assert any(url.endswith("coordinates.csv") for url in urls)
        assert any(url.endswith("network-comparison.svg") for url in urls)
        for url in urls:
            download = page.request.get(base + url)
            assert download.ok, (url, download.status)
        evidence["reviewed_deliverables"] = urls
        page.select_option("#strategy", "hydraulic")
        expect(page.locator("#export")).to_be_disabled()
        expect(page.locator("#downloads a")).to_have_count(0)
        expect(page.locator("#strategy-note")).to_contain_text("UNVERIFIED")
        evidence["settings_invalidate_review"] = True
        request, hydraulic = calculate()
        assert request["strategy"] == hydraulic["strategy"] == "hydraulic"
        assert hydraulic["after"]["hydraulic_evidence"]["status"] == "UNVERIFIED"
        assert hydraulic["after"]["hydraulic_evidence"]["aggregate_segment_loss_pa"] is None
        expect(page.locator("#copilot")).to_contain_text("UNVERIFIED objective terms: hydraulic")
        evidence["hydraulic_missing_evidence_is_explicit"] = True

        # Fresh calculated whole-network scene for reproducible review evidence.
        page.select_option("#strategy", "balanced")
        calculate()
        page.get_by_role("button", name="Isometric", exact=True).click()
        page.locator(".overview").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / "edge5-network-workspace.png"), full_page=True)
        page.locator(".viewport-panel").screenshot(path=str(artifacts / "edge5-network-viewport.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base + "/connected", wait_until="networkidle")
        expect(page.locator("#network-summary")).to_contain_text("nodes")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(artifacts / "edge5-mobile.png"))
        evidence["responsive_mobile"] = True
        evidence["page_errors"] = errors
        assert not errors, errors
        (artifacts / "browser-v5-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "before_length_mm": result["before"]["length_mm"], "after_length_mm": result["after"]["length_mm"], "changed_span_ids": result["changed_span_ids"], "reviewed_deliverables": len(urls), "page_errors": errors, "evidence": str(artifacts / "browser-v5-evidence.json")}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
