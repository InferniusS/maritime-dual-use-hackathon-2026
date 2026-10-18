"""Synthetic industrial browser acceptance; no CAD sources or private geometry."""
import json
import os
import shutil
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.environ.get("PIPEFORGE_UI_BASE_URL", "http://127.0.0.1:8000")
    artifacts = Path(os.environ.get("PIPEFORGE_UI_ARTIFACTS", str(Path(__file__).resolve().parents[1] / "local-output" / "browser-industrial")))
    artifacts.mkdir(parents=True, exist_ok=True)
    evidence = {}
    with sync_playwright() as playwright:
        executable = os.environ.get("PIPEFORGE_CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
        if os.name != "nt":
            options["args"] = ["--no-sandbox"]
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/connected", wait_until="networkidle")
        expect(page.locator("#network-summary")).to_contain_text("nodes", timeout=30000)

        def load(identifier, spans=None):
            started = time.perf_counter()
            page.select_option("#example", identifier)
            page.click("#load-example")
            page.wait_for_function("id=>JSON.parse(document.getElementById('network-json').value||'{}').id===id", arg=identifier)
            expect(page.locator("#optimize")).to_be_enabled(timeout=30000)
            if spans:
                page.wait_for_function("n=>JSON.parse(document.getElementById('network-plot').dataset.renderStats||'{}').spans===n", arg=spans)
            assert page.evaluate("network.provenance") == "synthetic"
            return time.perf_counter() - started

        def calculate(button="#optimize"):
            with page.expect_response(lambda response: response.url.endswith("/api/network/jobs")) as job:
                page.click(button)
            assert job.value.ok, job.value.text()
            expect(page.locator("#progress")).to_contain_text("Completed", timeout=150000)
            expect(page.locator("#error")).to_be_hidden()
            return page.evaluate("response.result")

        load_time = load("c-industrial-20", 20)
        components = page.evaluate("sourceGeometry.components")
        types = {item["type"] for item in components}
        assert {"valve", "flange"} <= types, types
        assert types & {"support", "hanger"}, types
        stats = page.locator("#network-plot").get_attribute("data-render-stats")
        assert json.loads(stats)["traces"] < 45
        page.select_option("#selection-scope", "compartments")
        expect(page.get_by_label("Optimize compartment machinery-room", exact=True)).to_be_checked()
        expect(page.locator("#scope-summary")).to_contain_text("20 spans may change")
        result = calculate()
        assert result["status"] == "IMPROVED", result["search"]
        assert len(result["changed_span_ids"]) >= 4
        assert result["validation"]["hard_failures"] == 0
        assert result["after"]["length_mm"] < result["before"]["length_mm"]
        progress = json.loads(page.locator("#search-progress").get_attribute("data-actual"))
        assert progress["evaluations"] == result["search"]["evaluations"]
        assert progress["expanded_states"] == result["search"]["expanded_states"]
        assert progress["total_neighborhoods"] >= 2
        expect(page.locator("#search-conclusion")).to_contain_text("not proof" if result["search"]["time_budget_reached"] else "not proven")
        # Select a rendered physical flange using its real Plotly point metadata.
        page.evaluate("""() => {
          const plot=document.getElementById('network-plot');
          const trace=plot.data.find(t=>t.type==='scatter3d'&&t.customdata?.some(p=>p?.physical_component?.type==='flange'));
          const index=trace.customdata.findIndex(p=>p?.physical_component?.type==='flange');
          plot.emit('plotly_click',{points:[{x:trace.x[index],y:trace.y[index],z:trace.z[index],customdata:trace.customdata[index]}]});
        }""")
        expect(page.locator("#selection")).to_contain_text("Selected physical flange")
        evidence["connected_20"] = {"load_wall_seconds": load_time, "render": json.loads(stats), "component_types": sorted(types), "component_count": len(components), "before_mm": result["before"]["length_mm"], "after_mm": result["after"]["length_mm"], "changed_span_ids": result["changed_span_ids"], "hard_failures": result["validation"]["hard_failures"], "actual_progress": progress}
        page.locator(".viewport-panel").screenshot(path=str(artifacts / "connected-20-physical.png"))

        load("e-congested-assembly", 21)
        congested = calculate()
        assert congested["status"] == "IMPROVED"
        assert len(congested["changed_span_ids"]) >= 4
        assert congested["validation"]["hard_failures"] == 0
        assert congested["after"]["length_mm"] < congested["before"]["length_mm"]
        evidence["industrial_congested_assembly"] = {"spans": 21, "before_mm": congested["before"]["length_mm"], "after_mm": congested["after"]["length_mm"], "changed_span_ids": congested["changed_span_ids"], "hard_failures": congested["validation"]["hard_failures"]}
        page.locator(".viewport-panel").screenshot(path=str(artifacts / "industrial-congested-assembly.png"))

        load_time = load("d-industrial-40", 40)
        page.wait_for_function("JSON.parse(document.getElementById('network-plot').dataset.renderStats||'{}').components>30")
        stats = json.loads(page.locator("#network-plot").get_attribute("data-render-stats"))
        assert stats["traces"] < 45, stats
        assert stats["mesh_vertices"] < 300000, stats
        page.get_by_role("button", name="Top · XY", exact=True).click()
        page.wait_for_function("document.getElementById('network-plot').dataset.rendering==='false'")
        assert page.evaluate("document.getElementById('network-plot')._fullLayout.scene.xaxis.range[1]") > 10000
        page.locator(".viewport-panel").screenshot(path=str(artifacts / "connected-40-batched.png"))
        evidence["connected_40_loading"] = {"load_wall_seconds": load_time, "render": stats}

        # A changed environment supersedes an in-flight solve. The source edit
        # and its independent findings must survive any late worker response.
        page.fill("#obstacle-id", "browser-live-edit")
        page.fill("#obstacle-min", "100, 100, 100")
        page.fill("#obstacle-max", "200, 200, 200")
        with page.expect_response(lambda response: response.url.endswith("/api/network/jobs")) as running:
            page.click("#optimize")
        old_job = running.value.json()["id"]
        page.click("#obstacle-add")
        expect(page.locator("#obstacle-change")).to_contain_text("ADD browser-live-edit", timeout=30000)
        page.wait_for_function("response===null&&network.obstacles.some(o=>o.id==='browser-live-edit')")
        expect(page.locator("#export")).to_be_disabled()
        expect(page.locator("#progress")).to_contain_text("Environment revalidation complete")
        old_state = page.request.get(base + "/api/network/jobs/" + old_job).json()
        for _ in range(100):
            if old_state["status"] not in ("queued", "running"):
                break
            page.wait_for_timeout(100)
            old_state = page.request.get(base + "/api/network/jobs/" + old_job).json()
        assert old_state["status"] == "cancelled", old_state["status"]
        evidence["environment_edit_cancels_stale_job"] = {"old_job_status": old_state["status"], "new_obstacle_retained": True, "old_proposal_discarded": True}

        load("a-already-good", 1)
        result = calculate()
        old_review = page.evaluate("({review_token:response.review_token,proposal_id:response.result.proposal_id})")
        page.check("#approve")
        expect(page.locator("#export")).to_be_enabled()
        page.fill("#obstacle-id", "browser-review-wall")
        page.select_option("#obstacle-kind", "wall")
        page.fill("#obstacle-min", "3400, 1000, 0")
        page.fill("#obstacle-max", "3900, 2000, 2500")
        with page.expect_response(lambda response: response.url.endswith("/api/network/obstacles/edit")) as edited:
            page.click("#obstacle-add")
        assert edited.value.ok, edited.value.text()
        change = edited.value.json()
        expect(page.locator("#obstacle-change")).to_contain_text("ADD browser-review-wall")
        assert change["validation"]["hard_failures"] > 0
        assert change["collision_delta"]["new_collision_pairs"]
        page.select_option("#obstacle-select", "")
        page.evaluate("""() => {
          const plot=document.getElementById('network-plot');
          const trace=plot.data.find(t=>t.type==='mesh3d'&&t.customdata?.some(p=>p?.id==='browser-review-wall'));
          const index=trace.customdata.findIndex(p=>p?.id==='browser-review-wall');
          plot.emit('plotly_click',{points:[{x:trace.x[index],y:trace.y[index],z:trace.z[index],customdata:trace.customdata[index]}]});
        }""")
        expect(page.locator("#obstacle-select")).to_have_value("browser-review-wall")
        expect(page.locator("#selection")).to_contain_text("Selected environment object · browser-review-wall")
        expect(page.locator("#export")).to_be_disabled()
        expect(page.locator("#downloads a")).to_have_count(0)
        expect(page.locator("#obstacle-move")).to_be_enabled()
        expect(page.locator("#checks")).to_contain_text("FAIL")
        stale = page.request.post(base + "/api/network/review", data={"network": change["network"], "approved": True, **old_review})
        assert stale.status == 409
        repaired = calculate("#optimize-affected")
        assert repaired["status"] == "IMPROVED", repaired["search"]
        assert repaired["validation"]["hard_failures"] == 0
        assert repaired["after"]["length_mm"] > repaired["before"]["length_mm"]
        evidence["wall_rerouting"] = {"environment_revision": change["environment_revision"], "affected_span_ids": change["affected_span_ids"], "new_collision_pairs": change["collision_delta"]["new_collision_pairs"], "before_mm": repaired["before"]["length_mm"], "after_mm": repaired["after"]["length_mm"], "hard_failures": repaired["validation"]["hard_failures"], "stale_review_rejected": stale.status}
        page.locator(".viewport-panel").screenshot(path=str(artifacts / "review-wall-rerouted.png"))
        page.fill("#obstacle-translation", "0, 3000, 0")
        page.click("#obstacle-move")
        expect(page.locator("#obstacle-change")).to_contain_text("MOVE browser-review-wall")
        expect(page.locator("#export")).to_be_disabled()
        assert page.evaluate("validation.hard_failures") == 0
        page.click("#obstacle-remove")
        expect(page.locator("#obstacle-change")).to_contain_text("REMOVE browser-review-wall")
        assert page.evaluate("network.obstacles.some(o=>o.id==='browser-review-wall')") is False
        evidence["review_obstacle_move_remove"] = True

        # A complete cross-section wall blocks all possible paths in the declared
        # volume. The UI must preserve the source and describe bounded evidence.
        bounds = page.evaluate("network.bounds")
        page.fill("#obstacle-id", "browser-sealed-wall")
        page.select_option("#obstacle-kind", "wall")
        page.fill("#obstacle-min", f"3400, {bounds['min'][1]}, {bounds['min'][2]}")
        page.fill("#obstacle-max", f"3900, {bounds['max'][1]}, {bounds['max'][2]}")
        page.click("#obstacle-add")
        expect(page.locator("#obstacle-change")).to_contain_text("ADD browser-sealed-wall")
        blocked = calculate("#optimize-affected")
        assert blocked["proposed_network"] is None
        assert blocked["status"] == "NO FEASIBLE ROUTE FOUND"
        expect(page.locator("#search-conclusion")).to_contain_text("not proven")
        evidence["sealed_environment"] = {"status": blocked["status"], "termination_reason": blocked["search"]["termination_reason"], "original_preserved": blocked["proposed_network"] is None}
        load("b-collision-repair")
        obstacles = page.evaluate("network.obstacles")
        protected = next(o for o in obstacles if not o.get("editable"))
        page.select_option("#obstacle-select", protected["id"])
        expect(page.locator("#obstacle-move")).to_be_disabled()
        expect(page.locator("#obstacle-remove")).to_be_disabled()
        evidence["protected_source_controls_disabled"] = True
        evidence["page_errors"] = errors
        assert not errors, errors
        (artifacts / "industrial-browser-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "industrial_20": evidence["connected_20"], "industrial_40": evidence["connected_40_loading"], "wall_rerouting": evidence["wall_rerouting"], "page_errors": errors, "evidence": str(artifacts / "industrial-browser-evidence.json")}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
