"""
Proceso de MarianMT (se ejecuta con el Python de herramientas/marian/venv).
Lee por stdin una lista JSON de frases por línea y responde con otra lista
JSON con las traducciones.
"""

import json
import os
import sys

import ctranslate2
import sentencepiece


def main():
    model_dir = sys.argv[1]
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    translator = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8", intra_threads=threads)
    sp_src = sentencepiece.SentencePieceProcessor(model_file=os.path.join(model_dir, "source.spm"))
    sp_tgt = sentencepiece.SentencePieceProcessor(model_file=os.path.join(model_dir, "target.spm"))

    out = sys.stdout.buffer
    out.write(b"ok\n")
    out.flush()

    for line in sys.stdin.buffer:
        texts = json.loads(line.decode("utf-8"))
        tokens = [sp_src.encode(t, out_type=str) + ["</s>"] for t in texts]
        results = translator.translate_batch(tokens, beam_size=4, max_batch_size=16,
                                             max_decoding_length=512)
        rv = []
        for r in results:
            toks = [t for t in r.hypotheses[0] if t not in ("</s>", "<pad>")]
            rv.append(sp_tgt.decode(toks))
        out.write((json.dumps(rv, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()


if __name__ == "__main__":
    main()
