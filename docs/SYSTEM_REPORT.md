# Production Optimizer — Báo cáo hệ thống

> Control plane tự động tối ưu code: đo baseline thật, đề xuất giải pháp có bằng chứng, tự implement, verify, đo lại, rồi quyết định giữ/sửa/hoàn tác — mọi bước đều được niêm phong lại thành artifact có thể kiểm chứng.

---

## 1. Tech Stack

| Lớp | Công nghệ |
|---|---|
| **Ngôn ngữ / runtime** | Python 3.13 |
| **Orchestration** | LangGraph (state machine dạng đồ thị, có checkpoint/resume) |
| **Data contract** | Pydantic v2 (163 model dữ liệu, có JSON Schema xuất tự động) |
| **Checkpoint / state lưu trữ** | PostgreSQL (qua `psycopg`), hoặc in-memory cho môi trường dev |
| **Artifact store** | S3 / MinIO (content-addressed, mỗi artifact có digest SHA-256) |
| **Policy engine** | Deterministic Python policy (fail-closed by default) |
| **Observability** | OpenTelemetry (traces theo case/node/artifact) |
| **LLM providers** | Anthropic, OpenAI, Gemini, DeepSeek, Ollama (local) — 1 adapter chung, chọn theo credential có sẵn |
| **Thực thi lệnh** | subprocess trực tiếp (build/lint/test) + Docker Compose (đo workload thật) |
| **Testing** | pytest — 440 test (unit + contract), skip-if-unreachable cho integration |
| **Lint / type-check** | ruff, pyright |

---

## 2. Kết quả đạt được (tổng quan)

| Hạng mục | Trạng thái |
|---|---|
| **163 business node** (99 lane A/B + C0, 64 shared workflow S01–S07) | ✅ Có handler production thật, không phải stub |
| **2 lane thu thập** (Lane A: người yêu cầu thủ công / Lane B: tự động phát hiện) | ✅ Logic đầy đủ, đã test |
| **7 giai đoạn shared workflow** (Rank → Plan → Implement → Verify → Remeasure → Decide → Report) | ✅ Chạy end-to-end thật qua CLI |
| **440 test tự động** | ✅ Pass, không cần hạ tầng ngoài (unit + contract) |
| **CLI thực chiến** (`scripts/run.py`) | ✅ Chạy được với repo thật, đa ngôn ngữ (Python/Go/.NET/Node...) |

**Điều đã kiểm chứng, không phải tuyên bố suông:** đã chạy thật lệnh `full` pipeline trên repository Node.js thật (Docker Compose), đo latency thật (`p95_latency_ms`), quan sát toàn bộ chuỗi implement → verify → remeasure → decide, kết thúc bằng báo cáo có số liệu thật (không tự bịa dữ liệu khi không đo được — hệ thống báo "unavailable" thay vì giả lập).

Chi tiết từng hạng mục xem mục 3 bên dưới.

---

## 3. Đã làm được / Chưa làm được (chi tiết)

### 3.1 Đã làm được

| # | Hạng mục | Mô tả |
|---|---|---|
| 1 | **163 node có handler thật** | Không phải khung sườn — mỗi node đọc artifact thật, gọi lệnh/model thật, niêm phong kết quả thật. Test bằng contract test (in-memory, không cần hạ tầng) cho từng stage. |
| 2 | **Đo baseline thật, không mô phỏng** | A2 chạy `subprocess` thật (build/lint/test) hoặc dựng Docker Compose thật để đo workload — không có bước nào "giả lập số liệu". |
| 3 | **Đa tiêu chí tối ưu có trọng số** | `--criteria metric:direction:target:unit:weight`, lặp lại nhiều lần. S01 chuẩn hóa trọng số (`weight / tổng`) rồi xếp hạng chiến lược theo lợi ích đa tiêu chí — không phải chỉ tối ưu 1 con số. |
| 4 | **Vòng lặp tự sửa lỗi có giới hạn (bounded repair loop)** | S03 → S04 (verify fail) → quay lại S03, tối đa 5 lần/phase. Không có khả năng chạy vô hạn — hết ngân sách thì dừng, báo cáo trung thực là chưa đạt. |
| 5 | **Human-in-the-loop approval** | Thay đổi có rủi ro cao (risk_tier `code`/`architecture`) dừng lại thật, chờ người duyệt (approve/reject), có thể resume sau khi checkpoint — không phải cơ chế giả (`auto-approve` chỉ dùng khi chạy CLI không tương tác). |
| 6 | **Rollback có thể thực thi thật** | Mỗi phase implementation bắt buộc khai `rollback_command` cụ thể (vd `git checkout -- <file>`) — khi S06 quyết định REVERT, lệnh này được chạy thật, không chỉ ghi log. |
| 7 | **Artifact chain có thể kiểm chứng (audit trail)** | Mọi output là bản ghi niêm phong (immutable), liên kết bằng SHA-256 digest — S07.10 verify lại toàn bộ chuỗi digest trước khi xuất báo cáo cuối. |
| 8 | **Workspace cô lập khi implement** | S03 luôn làm việc trên bản copy riêng, không bao giờ đụng vào working tree gốc của người dùng cho đến khi được duyệt apply thật (S06.90). |
| 9 | **Hỗ trợ 5 LLM provider + fallback an toàn** | Anthropic/OpenAI/Gemini/DeepSeek/Ollama qua 1 adapter chung; không có credential nào → dùng stand-in xác định (không bịa là "AI thật") để vẫn test được luồng. |
| 10 | **Checkpoint/resume thật** | PostgreSQL checkpointer lưu state giữa các bước — crash giữa chừng có thể resume lại đúng chỗ, không chạy lại từ đầu, không tạo trùng lặp side-effect. |

### 3.2 Chưa làm được

| # | Hạng mục | Mô tả / lý do |
|---|---|---|
| 1 | **Lane B chưa có lịch tự động** | Logic quét cơ hội (B1/B2) đã có và test đầy đủ, nhưng chưa có cron/webhook/watcher nào gọi tới — hiện chỉ chạy được qua CLI thủ công (`lane2`). |
| 2 | **B1.32–35 (query lịch sử metrics/logs/traces)** | Chưa có adapter kết nối vào hệ thống observability thật — các node này luôn trả về "không có dữ liệu" một cách trung thực, chưa từng lấy được tín hiệu thật từ hạ tầng giám sát có sẵn. |
| 3 | **Kubernetes worker broker chưa chạy trên cluster thật** | Code + unit test đầy đủ, nhưng chưa từng submit job lên K8s cluster thật — mọi script hiện tại đều chạy qua `LocalWorkerBroker` (chạy local, không phân tán). |
| 4 | **Không phân loại lý do fail ở C0** | Khi cổng hội tụ (C0) từ chối 1 case, chỉ có 1 kết quả `rejected` duy nhất — chưa định tuyến khác nhau theo loại lỗi (vd: mismatch digest → quay producer; thiếu solution → quay A3; nguồn lỗi thời → đóng case). |
| 5 | **Chưa có API công khai / MCP surface** | Chỉ chạy được qua CLI (`scripts/run.py`) — chưa có REST API hay giao diện tích hợp cho hệ thống khác gọi vào. |
| 6 | **`LocalWorkerBroker` không kill được subprocess khi timeout** | Job quá thời gian sẽ báo timeout đúng, nhưng tiến trình con vẫn có thể tiếp tục chạy ngầm — chưa có cơ chế đảm bảo dọn sạch. |
| 7 | **OPA policy adapter chưa triển khai** | Đang dùng deterministic Python policy làm chính; OPA (Open Policy Agent) qua HTTP đã lên kế hoạch nhưng chưa code. |

---

## 4. Kiến trúc & Flow hệ thống

### 4.1 Sơ đồ tổng quan

```
                 ┌──────────────────┐
  Yêu cầu thủ    │  A1 → A2 → A3    │──┐
  công ────────▶│  (phân tích yêu   │  │   ┌────┐
                 │  cầu → đo baseline│  ├──▶│ C0 │──▶ S01 → S02 ──▶ ┌─────────────┐
                 │  → đề xuất giải   │  │   └────┘   (chọn &        │ S03 → S04   │
                 │  pháp)            │  │            lên kế hoạch)  │   ↕    ↓    │
                 └──────────────────┘  │                            │ S05        │
                                        │                            │  ↓         │
                 ┌──────────────────┐  │                            │ S06        │
  Tự động phát   │  B1  →     B2    │──┘                            └──────┬──────┘
  hiện ─────────▶│  (quét cơ hội →  │                                      │
                 │  đủ điều kiện     │                              KEEP ──┴──▶ S07 → Báo cáo
                 │  thì tạo đề xuất) │                        FIX_ONE_PART ─┘  (quay lại S03)
                 └──────────────────┘                        REVERT ─────────┘  (quay lại S01)
```

### 4.2 Các thành phần chính

**Lane A / Lane B — thu thập bằng chứng**
| Giai đoạn | Việc làm |
|---|---|
| A1 / B1 | Phân tích yêu cầu / quét kho code tìm cơ hội tối ưu |
| A2 | **Đo baseline thật**: chạy build/lint/test thật, hoặc dựng Docker Compose đo hiệu năng thật (latency, throughput...) |
| A3 / B2 | Phân tích bằng chứng, đề xuất các chiến lược tối ưu có căn cứ (không được bịa lý do) |
| C0 | Cổng hội tụ: xác minh chuỗi digest, tính nhất quán schema trước khi cho đi tiếp |

**Shared workflow — thực thi & quyết định**
| Giai đoạn | Việc làm |
|---|---|
| S01 | Xếp hạng các chiến lược, chọn 1 (có thể dừng chờ người duyệt nếu rủi ro cao) |
| S02 | Soạn kế hoạch chi tiết (từng phase, rollback, tiêu chí hoàn thành) — có model đóng vai "phản biện" review chéo |
| S03 | **Thực thi thay đổi** trong workspace cô lập (copy riêng, không đụng vào code gốc cho tới khi được duyệt) |
| S04 | Verify: chạy lại build/lint/test thật để xác nhận patch không phá vỡ gì |
| S05 | Đo lại (remeasure) trong cùng điều kiện với baseline để so sánh công bằng |
| S06 | Quyết định: **KEEP** (giữ) / **FIX_ONE_PART** (sửa tiếp, quay lại S03) / **REVERT** (hoàn tác, quay lại S01) / **ESCALATE** |
| S07 | Xuất báo cáo cuối (JSON + Markdown), có đầy đủ số liệu, chi phí, và những gì chưa đạt được — không giấu thất bại |

### 4.3 Nguyên tắc thiết kế cốt lõi

1. **Không bịa bằng chứng** — mọi số liệu phải từ lệnh/đo lường thật sự chạy; không đo được thì báo "không có dữ liệu", không giả lập.
2. **Artifact niêm phong (immutable)** — mỗi bước tạo ra 1 bản ghi không thể sửa, liên kết với nhau bằng digest (SHA-256), giống chuỗi audit trail.
3. **An toàn khi lỗi (fail-closed)** — thiếu handler, thiếu quyền, model trả lời không hợp lệ → hệ thống dừng lại, không đoán mò tiếp.
4. **Vòng lặp có giới hạn** — mọi cơ chế thử-lại đều có ngân sách cố định (tối đa 5 lần sửa lỗi/phase), tránh chạy vô hạn.
5. **Con người vẫn kiểm soát điểm rủi ro cao** — thay đổi có rủi ro (`code`, `architecture`) luôn dừng lại chờ duyệt trước khi tiếp tục.

---

## 5. Vấn đề đang gặp phải

Hai điểm nghẽn thực tế lớn nhất khi triển khai hệ thống cho một codebase bất kỳ — không phải lỗi code, mà là **giới hạn cố hữu trong cách thiết kế hiện tại**.

### 5.1 Yêu cầu codebase phải có sẵn cấu hình Docker + script đo tương ứng

**Vấn đề cụ thể:**
Muốn đo được một metric có giá trị số thật (latency, throughput, memory usage...) thay vì chỉ pass/fail, hệ thống **bắt buộc** codebase phải có:
1. Một file Docker Compose dựng được toàn bộ ứng dụng (`--compose-file`)
2. Một script đánh giá (`--eval-command`) do đội dự án **tự viết sẵn**, chạy bên trong container, in ra đúng định dạng JSON hệ thống yêu cầu (`{"metrics": {"tên_metric": giá_trị}}`)

Nếu thiếu 1 trong 2 điều kiện này, hệ thống **không có cách nào tự đo được** con số hiệu năng thật — nó chỉ còn lại lựa chọn chạy `unit-command`/`build-command` trực tiếp trên host (không qua container), và khi đó chỉ thu được **exit code** (pass/fail), không có số liệu định lượng.

**Vì sao lại như vậy (căn cứ thiết kế):**
Nguyên tắc "không bịa bằng chứng" (mục 4.3, #1) buộc hệ thống phải từ chối tự suy đoán cách đo hiệu năng của một ứng dụng bất kỳ — mỗi codebase có kiến trúc, endpoint, cách khởi động khác nhau, không có công thức benchmark tổng quát nào áp dụng được cho mọi trường hợp một cách an toàn. Nên hệ thống **giao trách nhiệm viết công cụ đo cho đội dự án**, thay vì tự đoán mò.

**Hệ quả thực tế:**
- Với codebase **đã có sẵn** hạ tầng test/benchmark (CI pipeline dùng Docker, có script load-test...) → tận dụng lại được ngay, chi phí thấp.
- Với codebase **legacy hoặc chưa containerize** → phải đầu tư viết thêm: Dockerfile, Compose file, và script đo — đây là **rào cản triển khai lớn nhất** hiện tại, đặc biệt với hệ thống cũ không có văn hóa container hóa.
- Ngoài ra: các lệnh `build/lint/unit-command` (không phải `--eval-command`) chạy **trực tiếp trên host** chạy `scripts/run.py`, không qua Docker — nếu môi trường build/test của dự án cần các service phụ trợ phức tạp (DB, message queue, biến môi trường đặc thù) mà chưa cấu hình sẵn trên host, các lệnh này sẽ fail dù bản thân code không có lỗi.

**Hướng giảm nhẹ khả thi:** cho phép các lệnh `build/lint/unit-command` cũng chạy qua `docker compose run` (đội dự án tự viết wrapper), tận dụng cùng 1 hạ tầng Docker đã dựng cho `--eval-command` — chưa được tự động hóa, hiện phải làm thủ công.

### 5.2 Phụ thuộc vào chất lượng LLM

**Vấn đề cụ thể:**
4 điểm trong pipeline dùng LLM để ra quyết định:
- **S02.30** — soạn kế hoạch triển khai (phase, task, rollback)
- **S02.80** — phản biện độc lập, kiểm tra bỏ sót/rủi ro của kế hoạch
- **S03.50** — agent thực thi thay đổi code thật (đọc file, sửa file, chạy lệnh)
- **S07.70** — viết tường thuật báo cáo cuối

Khi dùng model nhỏ/rẻ (đã thử nghiệm: Gemini Flash-Lite, Ollama model 3–4B tham số), quan sát thực tế:
- **Kế hoạch (S02.30) hay bị từ chối lặp lại** — model quên không khai `rollback_command`, không giải thích chiến lược cache invalidation khi cần, hoặc **tự bịa đường dẫn file không tồn tại** trong repo (path hallucination) — dù đã được cung cấp đúng danh sách file thật trong context.
- **Model 3–4B qua Ollama không ổn định với structured output (tool-calling)** — dễ timeout hoặc trả về JSON không hợp lệ khi context dài (kế hoạch nhiều phase, nhiều tiêu chí), phải retry hoặc dừng hẳn.
- **Agent thực thi (S03.50)** có thể hiểu sai phạm vi cho phép sửa, dẫn tới patch bị từ chối toàn bộ (rule "vi phạm scope thì reject cả patch, không cắt gọt một phần") — tốn thêm 1 vòng lặp sửa lỗi dù bản chất ý tưởng đúng.

**Vì sao lại như vậy:**
Hệ thống có 2 lớp phòng thủ (prompt nêu rõ luật + gate kiểm tra deterministic sau đó), nhưng **prompt không thể ép buộc model tuân thủ 100%** — đây là giới hạn cố hữu của LLM, không phải lỗi thiết kế. Gate deterministic (S02.70/S02.81/S03.60...) đã ngăn được các plan/patch sai lọt qua, nhưng cái giá là **tốn thêm lượt thử lại** (tối đa 2 lần cho plan, 5 lần cho implementation) — với model yếu, xác suất phải dùng hết cả ngân sách thử lại này cao hơn hẳn.

**Hệ quả thực tế:**
- Model mạnh (Claude Sonnet, GPT-4 class, Gemini Pro) → tỷ lệ pass ngay lần đầu cao, ít vòng lặp, chi phí vận hành ổn định.
- Model yếu/miễn phí → thường xuyên chạm giới hạn thử lại, case kết thúc ở trạng thái "chưa đạt được mục tiêu trong ngân sách" dù bản chất bài toán khả thi — không phải lỗi hệ thống, mà là model không đủ năng lực soạn plan/code đạt chuẩn.
- Vì đây là **rủi ro đã biết trước**, hệ thống không cố "vá" bằng cách nới lỏng gate kiểm tra (sẽ vi phạm nguyên tắc không bịa bằng chứng) — chấp nhận việc dừng lại trung thực còn hơn cho qua một kế hoạch/patch không đạt chuẩn.

**Hướng giảm nhẹ khả thi:** với môi trường production, nên ưu tiên dùng model tier cao (Claude Sonnet/Opus, GPT-4 class) cho ít nhất S02.30 và S03.50 — đây là 2 điểm nhạy cảm nhất với chất lượng model; model nhỏ chỉ phù hợp cho việc test luồng hệ thống, chưa phù hợp chạy thật trên codebase phức tạp.

---

## 6. Cách vận hành thực tế (tóm tắt)

```bash
python scripts/run.py full <đường-dẫn-repo> \
    --feature-id <tên-tính-năng> \
    --criteria <metric>:<hướng>:<mục-tiêu>:<đơn-vị>:<trọng-số> \
    --execution-profile docker_compose \
    --compose-file docker-compose.yaml \
    --eval-command <lệnh-đo-thật> \
    --model-provider <anthropic|openai|gemini|deepseek|ollama>
```

Một lệnh duy nhất chạy trọn vẹn từ phân tích yêu cầu → đo baseline → đề xuất → lập kế hoạch → thực thi → verify → quyết định → báo cáo. Toàn bộ tiến trình có thể dừng giữa chừng và tiếp tục lại nhờ checkpoint (PostgreSQL), không mất tiến độ khi crash.

---

*Chi tiết đầy đủ (flag, ví dụ, giới hạn kỹ thuật) xem tại `README.md` ở gốc repository.*
