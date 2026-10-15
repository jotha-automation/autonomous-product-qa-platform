"""Deterministic mock LLM responses for local dev and CI."""
import json
from typing import Any, Dict, Optional


def mock_response(agent_name: str, prompt: str) -> str:
    """Return JSON string tailored to agent class name."""
    name = agent_name.lower()

    if "readiness" in name:
        docs_hint = "acceptance" in prompt.lower() or "ac" in prompt.lower()
        if docs_hint or "brd" in prompt.lower():
            return json.dumps(
                {
                    "status": "READY",
                    "missing_artifacts": [],
                    "present_types": ["BRD", "PRD", "UC", "AC"],
                    "block_run": False,
                }
            )
        return json.dumps(
            {
                "status": "WARN",
                "missing_artifacts": ["AC"],
                "present_types": ["BRD", "PRD", "UC"],
                "block_run": False,
                "score": 0.72,
            }
        )

    if "requirements" in name:
        return json.dumps(
            {
                "requirements": [
                    {"id": "REQ-001", "description": "User can log in", "priority": "high"},
                    {"id": "REQ-002", "description": "User can reset password", "priority": "medium"},
                ],
                "features": ["Authentication", "Password Recovery"],
                "actors": ["End User", "Admin"],
                "business_rules": ["Password must be 8+ characters"],
            }
        )

    if "coverage" in name:
        return json.dumps(
            {
                "gaps": [{"requirement_id": "REQ-002", "gap": "No UC for password reset"}],
                "coverage_gaps": [{"requirement_id": "REQ-002", "gap": "No UC for password reset"}],
                "duplicates": [],
                "conflicts": [],
                "coverage_percent": 0.85,
                "coverage_score": 0.85,
            }
        )

    if "testdesign" in name or "test_design" in name:
        portfolio = "portfolio" in prompt.lower() or "jsh" in prompt.lower()
        if portfolio:
            scenarios = [
                {
                    "id": "FTC-001",
                    "title": "Home page loads and hero visible",
                    "priority": "P0",
                    "requirement_ids": ["REQ-001"],
                    "steps": ["Open BASE_URL", "Wait for hero", "Verify navigation links"],
                    "expected_results": ["Page loads under 3s", "Hero and nav visible"],
                    "negative_paths": ["Offline network"],
                    "test_types": ["smoke"],
                },
                {
                    "id": "FTC-002",
                    "title": "Contact form valid submission",
                    "priority": "P1",
                    "requirement_ids": ["REQ-002"],
                    "steps": ["Scroll to contact", "Fill required fields", "Submit"],
                    "expected_results": ["Success feedback shown"],
                    "negative_paths": ["Empty required fields"],
                    "test_types": ["regression"],
                },
                {
                    "id": "FTC-003",
                    "title": "Language toggle EN/ES",
                    "priority": "P1",
                    "requirement_ids": ["REQ-003"],
                    "steps": ["Click language control", "Verify labels change"],
                    "expected_results": ["UI strings match selected locale"],
                    "negative_paths": [],
                    "test_types": ["regression"],
                },
            ]
        else:
            scenarios = [
                {
                    "id": "FTC-001",
                    "title": "Successful login",
                    "priority": "P1",
                    "requirement_ids": ["REQ-001"],
                    "steps": ["Given valid credentials", "When user submits", "Then dashboard loads"],
                    "expected_results": ["Dashboard visible"],
                    "test_types": ["smoke"],
                },
            ]
        return json.dumps({"functional_scenarios": scenarios, "coverage_targets": ["REQ-001", "REQ-002"]})

    if "traceability" in name:
        return json.dumps(
            {
                "matrix": [
                    {"requirement_id": "REQ-001", "test_id": "FTC-001", "coverage": "full"},
                    {"requirement_id": "REQ-002", "test_id": "FTC-002", "coverage": "partial"},
                ]
            }
        )

    if "risk" in name:
        return json.dumps(
            {
                "risk_scores": {"functional": 0.2, "security": 0.15},
                "release_risk": "low",
                "risk_factors": ["Partial traceability on REQ-002"],
            }
        )

    if "knowledge" in name:
        return json.dumps(
            {
                "matches": [
                    {"requirement_id": "REQ-001", "similarity": 0.92, "description": "Login flow"},
                ]
            }
        )

    if "executive" in name or "reporting" in name:
        return json.dumps(
            {
                "executive_summary": "Release readiness is acceptable with minor gaps.",
                "readiness_score": 78,
                "recommendations": ["Complete AC for password reset edge cases"],
            }
        )

    if "architect" in name:
        return json.dumps(
            {
                "suite": {"name": "portfolio-e2e", "feature": "JSH Portfolio", "browser_matrix": ["chromium"]},
                "pom_structure": {
                    "pages": [
                        {"name": "HomePage", "route": "/", "responsibilities": ["hero", "nav"]},
                        {"name": "ContactPage", "route": "/#contact", "responsibilities": ["form"]},
                    ],
                    "fixtures": ["test-fixtures"],
                },
                "scenarios": [
                    {
                        "id": "E2E-001",
                        "name": "smoke-home",
                        "priority": "P0",
                        "page_object": "HomePage",
                        "requirement_ids": ["REQ-001"],
                        "steps": [
                            {"action": "goto", "target": "BASE_URL", "data": ""},
                            {"action": "wait", "target": "networkidle", "data": ""},
                        ],
                        "assertions": [{"type": "visible", "target": "nav", "expected": "true"}],
                        "locators": {"primary": "[data-testid=main-nav]", "fallback": "role=navigation"},
                        "tags": ["smoke"],
                    },
                    {
                        "id": "E2E-002",
                        "name": "contact-form",
                        "priority": "P1",
                        "page_object": "ContactPage",
                        "requirement_ids": ["REQ-002"],
                        "steps": [
                            {"action": "click", "target": "[data-testid=contact-link]", "data": ""},
                            {"action": "fill", "target": "[data-testid=contact-email]", "data": "qa@example.com"},
                        ],
                        "assertions": [{"type": "visible", "target": "[data-testid=contact-form]", "expected": "true"}],
                        "locators": {"primary": "[data-testid=contact-form]", "fallback": "form"},
                        "tags": ["regression"],
                    },
                ],
                "test_data": {"valid_contact": {"email": "qa@example.com"}},
                "non_functional": {"max_load_ms": 3000, "retry_policy": "1"},
            }
        )

    if "generator" in name:
        return json.dumps(
            {
                "pages": {
                    "HomePage": {
                        "methods": [
                            {
                                "name": "navigate",
                                "body": "await this.page.goto(process.env.BASE_URL!); await this.page.waitForLoadState('domcontentloaded');",
                            },
                            {
                                "name": "executeScenario",
                                "body": "await this.page.getByRole('navigation').waitFor({ state: 'visible' });",
                            },
                        ]
                    }
                },
                "specs": {
                    "E2E-001": {
                        "test_body_lines": [
                            "await pom.navigate();",
                            "await pom.executeScenario();",
                            "await expect(page.getByRole('navigation')).toBeVisible();",
                        ],
                        "timeout_ms": 60000,
                    }
                },
            }
        )

    if "execution" in name:
        return json.dumps(
            {
                "status": "completed",
                "passed": 2,
                "failed": 0,
                "duration_ms": 1200,
                "artifacts": {"screenshots": [], "traces": [], "videos": []},
            }
        )

    if "failure_rca" in name or "rca" in name:
        return json.dumps(
            {
                "root_cause": "Primary locator not found within timeout — DOM structure or data-testid missing",
                "confidence": 0.84,
                "failure_category": "locator",
                "failure_summary": {
                    "title": "E2E locator timeout",
                    "severity": "high",
                    "affected_tests": ["smoke-home"],
                },
                "likely_root_cause": {
                    "description": "Page object uses brittle selector; app uses different markup",
                    "confidence": 0.84,
                    "category": "locator",
                    "evidence": ["Timeout 15000ms waiting for locator"],
                },
                "five_whys": [
                    "Test failed waiting for element",
                    "Locator did not match DOM",
                    "data-testid not present in build",
                    "Test not aligned with PRD selectors",
                    "Need POM locator hardening",
                ],
                "action_items": [
                    {"priority": "P0", "action": "Align page object with data-testid from PRD", "owner": "qa", "status": "pending"},
                ],
                "healing_candidates": [
                    {"file": "pages/HomePage.ts", "locator_hint": "data-testid=main-nav", "confidence": 0.8},
                ],
            }
        )

    if "healing" in name or "self_healing" in name:
        return json.dumps(
            {
                "healed": True,
                "confidence": 0.78,
                "changes": [
                    {
                        "file": "pages/HomePage.ts",
                        "old": "this.page.locator('body')",
                        "new": "this.page.getByTestId('main-nav')",
                        "rationale": "RCA: replace brittle body locator",
                    }
                ],
                "skipped": [],
            }
        )

    if "defect" in name:
        return json.dumps(
            {
                "recommendations": [
                    "Fix P0 locator failures before release",
                    "Add data-testid attributes in application for stable automation",
                ],
                "release_blocker": True,
                "release_readiness": "no_go",
                "priority_order": ["DEF-001"],
                "defects_enriched": [{"id": "DEF-001", "severity": "high", "owner": "qa"}],
            }
        )

    if "intakeinterpretation" in name:
        return json.dumps(
            {
                "interpretation_method": "inferred",
                "confidence": 0.75,
                "target_url": "https://shop.acme.example.com",
                "project_id": "ACME-SHOP-R2",
                "release": "R2",
                "documents": [
                    {
                        "document_type": "BRD",
                        "title": "Business Requirements",
                        "raw_text": "Business objective: reduce cart abandonment for Acme Shop.",
                        "confidence": 0.8,
                        "inferred": True,
                    },
                    {
                        "document_type": "PRD",
                        "title": "Product Requirements",
                        "raw_text": "Functional: login, search, cart, checkout, coupons, email confirmation.",
                        "confidence": 0.8,
                        "inferred": True,
                    },
                    {
                        "document_type": "UC",
                        "title": "Use Cases",
                        "raw_text": "UC-001 User completes purchase. Actor: Customer.",
                        "confidence": 0.75,
                        "inferred": False,
                    },
                    {
                        "document_type": "AC",
                        "title": "Acceptance Criteria",
                        "raw_text": "Given logged-in user with cart When checkout Then confirmation appears.",
                        "confidence": 0.75,
                        "inferred": False,
                    },
                ],
                "notes": "Mock intake interpretation",
            }
        )

    if "clientintakeinterpreter" in name:
        return json.dumps(
            {
                "obligations": [
                    {
                        "id": "OBL-001",
                        "title": "Validate login and dashboard access",
                        "preconditions": ["Valid staging credentials available"],
                        "steps": ["Given valid user When login Then dashboard loads"],
                        "expected_results": ["Dashboard visible"],
                        "automation_tier": "automated",
                        "required_assets": ["login_user"],
                        "source_filename": "qa-spec.pdf",
                        "excerpt": "Caso de Prueba EMPRESARIAL-13327",
                    },
                    {
                        "id": "OBL-002",
                        "title": "Validate business-day rejection rule",
                        "preconditions": ["ACH inscription in sent state"],
                        "steps": ["Wait two business days without bank response"],
                        "expected_results": ["Status rejected with communication error"],
                        "automation_tier": "manual",
                        "required_assets": [],
                        "source_filename": "qa-spec.pdf",
                        "excerpt": "días hábiles",
                    },
                ]
            }
        )

    if "qaobligationextractor" in name:
        return json.dumps(
            {
                "obligations": [
                    {
                        "id": "OBL-001",
                        "title": "Validate login and dashboard access",
                        "preconditions": ["Valid staging credentials available"],
                        "steps": ["Given valid user When login Then dashboard loads"],
                        "expected_results": ["Dashboard visible"],
                        "automation_tier": "automated",
                    },
                    {
                        "id": "OBL-002",
                        "title": "Validate business-day rejection rule",
                        "preconditions": ["ACH inscription in sent state"],
                        "steps": ["Wait two business days without bank response"],
                        "expected_results": ["Status rejected with communication error"],
                        "automation_tier": "manual",
                    },
                ]
            }
        )

    return json.dumps({"status": "ok", "agent": agent_name})


class MockAzureFoundryClient:
    """Drop-in mock when APTP_USE_MOCK_LLM=true."""

    async def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        cache_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        response_format_json: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        cache_ttl_seconds: Optional[int] = None,
        deployment_override: Optional[str] = None,
    ) -> str:
        agent = (metadata or {}).get("agent", "unknown")
        return mock_response(agent, prompt)

    async def health_check(self) -> dict:
        return {"status": "ok", "mode": "mock"}
