"""The Docker image's inputs must stay pinned and the published image must carry attestations."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
DEPENDABOT = REPO / ".github" / "dependabot.yml"
PUBLISH_DOCKER = REPO / ".github" / "workflows" / "publish-docker.yml"

# A readable tag plus the digest, e.g. python:3.12-slim@sha256:<64 hex>
PINNED = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def _from_images():
    images = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts and parts[0].upper() == "FROM":
            images.append(parts[1])
    return images


def test_every_base_image_is_pinned_by_tag_and_digest():
    images = _from_images()
    assert images
    for image in images:
        assert PINNED.match(image), image


def test_copy_from_uses_a_stage_not_an_unpinned_image():
    # Dependabot only reads FROM lines, so images are pulled in through named stages.
    stages = {
        parts[3].lower()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if (parts := line.split()) and parts[0].upper() == "FROM" and len(parts) == 4 and parts[2].upper() == "AS"
    }
    sources = re.findall(r"COPY\s+--from=(\S+)", DOCKERFILE.read_text(encoding="utf-8"))
    assert sources
    for source in sources:
        assert source.lower() in stages, source


def test_dependabot_watches_docker_and_github_actions():
    config = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert config["version"] == 2
    ecosystems = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert ("docker", "/") in ecosystems
    assert ("github-actions", "/") in ecosystems


def test_published_image_carries_provenance_and_sbom():
    workflow = yaml.safe_load(PUBLISH_DOCKER.read_text(encoding="utf-8"))
    assert workflow["permissions"]["packages"] == "write"
    steps = workflow["jobs"]["docker"]["steps"]
    uses = [step.get("uses", "") for step in steps]
    buildx = next(i for i, u in enumerate(uses) if u.startswith("docker/setup-buildx-action@"))
    build = next(i for i, u in enumerate(uses) if u.startswith("docker/build-push-action@"))
    # Attestations need the docker-container driver that setup-buildx-action creates.
    assert buildx < build
    options = steps[build]["with"]
    assert options["provenance"] == "mode=max"
    assert options["sbom"] is True
