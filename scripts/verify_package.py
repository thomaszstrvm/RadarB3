#!/usr/bin/env python3
"""Verifica o pacote de planejamento, não o aplicativo futuro.

Sem dependências. Valida apenas os recursos JSON Schema usados nestes três
schemas (type, properties, required, additionalProperties, items, enum, const).
Não é um validador geral de JSON Schema. Na implementação usar biblioteca
completa e validação semântica dos contratos.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def is_type(value, kind):
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }[kind]()


def validate(value, schema, path="$", check_only=False):
    allowed = {"$schema", "title", "type", "properties", "required", "additionalProperties", "items", "enum", "const"}
    require(not set(schema) - allowed, f"{path}: recurso de schema não suportado pelo verificador")
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not check_only:
        require(any(is_type(value, t) for t in types), f"{path}: tipo inválido")
        if "enum" in schema:
            require(value in schema["enum"], f"{path}: enum inválido")
        if "const" in schema:
            require(value == schema["const"], f"{path}: const inválida")
    if "object" in types:
        props = schema["properties"]
        require(schema.get("additionalProperties") is False, f"{path}: contrato não estrito")
        require(set(schema["required"]) == set(props), f"{path}: campos obrigatórios inconsistentes")
        if not check_only and value is not None:
            require(set(value) == set(props), f"{path}: campos ausentes/extras {set(value) ^ set(props)}")
        for name, child in props.items():
            validate(None if check_only else value[name], child, f"{path}.{name}", check_only)
    if "array" in types:
        if check_only:
            validate(None, schema["items"], f"{path}[]", True)
        else:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp sem timezone")
    return parsed


def main():
    manifest = read_json("MANIFEST.json")
    listed = {item["path"] for item in manifest["files"]}
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and p.name != "MANIFEST.json" and "__pycache__" not in p.parts}
    require(listed == actual, f"Inventário divergente: {listed ^ actual}")
    for entry in manifest["files"]:
        file = ROOT / entry["path"]
        require(file.stat().st_size == entry["size_bytes"], f"Tamanho alterado: {entry['path']}")
        require(hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"], f"Hash alterado: {entry['path']}")
    json_files = list(ROOT.rglob("*.json"))
    for file in json_files:
        json.loads(file.read_text(encoding="utf-8"))
    for name in ("analysis", "event", "evidence-bundle"):
        schema = read_json(f"schemas/{name}.schema.json")
        validate(None, schema, check_only=True)
        validate(read_json(f"fixtures/{name}.example.json"), schema)
    bundle = read_json("fixtures/evidence-bundle.example.json")
    analysis = read_json("fixtures/analysis.example.json")
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    require(len(evidence) == len(bundle["evidence"]), "IDs de evidências duplicados")
    require(analysis["as_of"] == bundle["as_of"], "as_of divergente")
    require(analysis["asset_id"] == bundle["asset"]["asset_id"], "Ativo divergente")
    cutoff = instant(bundle["as_of"])
    start = instant(bundle["window"]["start"])
    require(start <= cutoff == instant(bundle["window"]["end"]), "Janela inválida")
    for e in evidence.values():
        require(hashlib.sha256(e["content"].encode()).hexdigest() == e["content_sha256"], "Hash de conteúdo inválido")
        for field in ("first_seen_at", "fetched_at", "available_at"):
            require(instant(e[field]) <= cutoff, f"Evidência futura: {field}")
        require(instant(e["available_at"]) >= instant(e["first_seen_at"]), "Disponibilidade anterior à descoberta")
        if e["record_type"] == "article" and e["published_at"]:
            require(start <= instant(e["published_at"]) <= cutoff, "Notícia fora da janela")
    for metric in bundle["computed_metrics"]:
        require(set(metric["evidence_ids"]) <= evidence.keys(), "Insumo de métrica inexistente")
        if metric["value"] is None:
            require(metric["missing_reason"] and metric["evidence_id"] is None, "Métrica ausente sem motivo")
        else:
            backing = evidence.get(metric["evidence_id"])
            require(backing and backing["record_type"] == "computed_metric", "Métrica sem evidência calculada")
    used = set(analysis["opportunity"]["evidence_ids"])
    for item in analysis["claims"] + analysis["scenarios"]:
        require(item["evidence_ids"], "Afirmação/cenário sem evidência")
        used.update(item["evidence_ids"])
    require(used == set(analysis["evidence_ids"]) and used <= evidence.keys(), "Referências divergentes")
    require(all(s["horizon_days"] in bundle["forecast_horizons_days"] for s in analysis["scenarios"]), "Horizonte inválido")
    if analysis["status"] == "insufficient_data":
        require(analysis["opportunity"]["classification"] == "insufficient_data", "Abstenção inconsistente")
    sources = read_json("config/news-sources.json")["sources"]
    require(len(sources) == len({s["id"] for s in sources}) == 8, "O catálogo deve ter oito fontes únicas")
    require(sum(s["category"] == "finance" for s in sources) == 2, "Devem existir duas fontes financeiras")
    config = read_json("config/project.example.json")
    require(config["news_lookback"]["unit"] == "calendar_months" and config["news_lookback"]["value"] == 3, "Janela incorreta")
    require(config["news_lookback"]["start_time_policy"] == "start_of_local_day", "Corte inicial inconsistente")
    require(config["ai"]["concurrency"] == 1 and not config["ai"]["paid_fallback"], "Política de IA incorreta")
    require(config["free_only"] and not config["analysis"]["orders_enabled"], "Escopo incorreto")
    ET.parse(ROOT / "design/layout-reference.svg")
    cases = read_json("fixtures/scenario_cases.json")["cases"]
    require(len({c["id"] for c in cases}) == len(cases), "Casos duplicados")
    print(f"OK: {len(listed)} arquivos inventariados + manifesto; {len(json_files)} JSONs válidos; 3 schemas e exemplos compatíveis.")
    print(f"OK: invariantes do exemplo, 8 fontes, 1 modelo, janela de 3 meses e SVG; {len(cases)} casos de aceite especificados.")
    print("Não executa testes do aplicativo nem homologa provedores externos.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(f"FALHA: {error}", file=sys.stderr)
        sys.exit(1)
