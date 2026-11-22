module edgemac_formal #(
    parameter integer LANES = 1,
    parameter integer MAX_LENGTH = 8,
    // PDR does not converge on the beat-count arithmetic at 1024 elements.
    parameter integer CHECK_RESPONSE = 1
) (
    input wire clk,
    input wire rst_n,
    input wire cfg_valid,
    input wire cfg_write,
    input wire [7:0] cfg_addr,
    input wire [31:0] cfg_wdata,
    input wire in_valid,
    input wire [LANES*8-1:0] activations,
    input wire [LANES*8-1:0] weights,
    input wire [LANES-1:0] in_mask,
    input wire result_ready
);
    wire cfg_ready;
    wire [31:0] cfg_rdata;
    wire cfg_error;
    wire in_ready;
    wire result_valid;
    wire signed [31:0] result_data;

    edgemac_top #(.LANES(LANES), .MAX_VECTOR_LENGTH(MAX_LENGTH)) dut (.*);

    reg initialized = 0;
    reg [31:0] length;
    reg pending;
    reg completed;
    reg [10:0] remaining;
    reg [1:0] errors;
    reg seen_ack;
    // Bounded response: accepted legal beats since the last successful start.
    reg [10:0] beats;
    reg [31:0] started_length;
    wire [31:0] required_beats = (started_length + LANES - 1) / LANES;

    wire idle = (remaining == 0) && !pending;
    wire [10:0] beat_size = (remaining > LANES) ? LANES : remaining;
    wire [LANES-1:0] legal_mask;
    for (genvar lane = 0; lane < LANES; lane = lane + 1) begin : mask_lanes
        assign legal_mask[lane] = (lane < beat_size);
    end
    wire requested_start = cfg_valid && cfg_write && cfg_addr == 0 && cfg_wdata == 1;
    wire legal_length = (length >= 1 && length <= MAX_LENGTH);
    wire accepted_beat = in_valid && in_ready;

    always @(posedge clk) begin
        initialized <= 1;
        if (!initialized) assume(!rst_n);

        if (!rst_n) begin
            length <= 0;
            remaining <= 0;
            pending <= 0;
            completed <= 0;
            errors <= 0;
            seen_ack <= 0;
            beats <= 0;
            started_length <= 0;
            assert(!cfg_ready && !in_ready && !result_valid);
        end else begin
            // The checker tracks the remaining operand budget instead of DUT states.
            assert(cfg_ready);
            assert(in_ready == (remaining != 0));
            assert(result_valid == pending);
            assert(remaining <= MAX_LENGTH);
            assert(!pending || (remaining == 0));
            // Operands remaining means fewer than ceil(N/LANES) beats were consumed, and a
            // pending result was produced by exactly that many. With in_ready proven high
            // while operands remain, a producer presenting legal beats gets a result.
            if (CHECK_RESPONSE) begin
                assert(remaining == 0 || beats < required_beats);
                if (pending)
                    assert(beats == required_beats);
            end
            if (requested_start)
                assert(cfg_error == (!idle || !legal_length));
            if (cfg_valid && !cfg_write && cfg_addr == 8'h04)
                assert(!cfg_error && cfg_rdata == {29'b0, completed, pending, !idle});
            if (cfg_valid && !cfg_write && cfg_addr == 8'h14)
                assert(!cfg_error && cfg_rdata == {30'b0, errors});
            if (cfg_valid && !cfg_write && cfg_addr == 8'h10 && pending)
                assert(!cfg_error && cfg_rdata == result_data);
            if (initialized && $past(rst_n && result_valid && !result_ready)) begin
                assert(result_valid);
                assert(result_data == $past(result_data));
            end

            if (cfg_valid && cfg_write && cfg_addr == 8'h14 && cfg_wdata[31:2] == 0)
                errors <= errors & ~cfg_wdata[1:0];
            if (cfg_valid && cfg_write && cfg_addr == 8'h08 && idle)
                length <= cfg_wdata;
            if (requested_start && idle) begin
                if (legal_length) begin
                    remaining <= length[10:0];
                    completed <= 0;
                    beats <= 0;
                    started_length <= length;
                end else begin
                    errors[0] <= 1;
                end
            end
            if (accepted_beat) begin
                if (in_mask == legal_mask) begin
                    remaining <= remaining - beat_size;
                    beats <= beats + 1'b1;
                    if (remaining == beat_size) begin
                        pending <= 1;
                        completed <= 1;
                    end
                end else begin
                    remaining <= 0;
                    errors[1] <= 1;
                end
            end
            if (pending && result_ready) begin
                pending <= 0;
                seen_ack <= 1;
            end
        end
        if (initialized) begin
            cover(rst_n && seen_ack && result_valid);
            cover(rst_n && result_valid && !result_ready);
            cover(rst_n && errors[1] && idle);
            cover(rst_n && pending && started_length == MAX_LENGTH);
            cover(!rst_n && $past(rst_n && result_valid && !result_ready));
        end
    end

    if (LANES > 1) begin : partial_reachable
        always @(posedge clk) begin
            if (initialized)
                cover(rst_n && accepted_beat && remaining == 3 && in_mask == legal_mask);
        end
    end
endmodule
