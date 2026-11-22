`timescale 1ns/1ps

module edgemac_top #(
    parameter int LANES = 1,
    parameter int MAX_VECTOR_LENGTH = 1024
) (
    input  logic                     clk,
    input  logic                     rst_n,

    input  logic                     cfg_valid,
    input  logic                     cfg_write,
    input  logic [7:0]               cfg_addr,
    input  logic [31:0]              cfg_wdata,
    output logic                     cfg_ready,
    output logic [31:0]              cfg_rdata,
    output logic                     cfg_error,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [8*LANES-1:0]       activations,
    input  logic [8*LANES-1:0]       weights,
    input  logic [LANES-1:0]         in_mask,

    output logic                     result_valid,
    input  logic                     result_ready,
    output logic signed [31:0]       result_data
);
    localparam int LENGTH_WIDTH = $clog2(MAX_VECTOR_LENGTH + 1);
    localparam int COUNT_WIDTH = LENGTH_WIDTH < 3 ? 3 : LENGTH_WIDTH;
    localparam logic [COUNT_WIDTH-1:0] LANE_COUNT = COUNT_WIDTH'(LANES);
    localparam logic [31:0] LENGTH_LIMIT = 32'(MAX_VECTOR_LENGTH);

    typedef enum logic [1:0] {IDLE, RECEIVE, HOLD} state_t;
    typedef logic signed [32:0] total_t;

    state_t state_q;
    total_t accumulator_q, next_total;
    logic [COUNT_WIDTH-1:0] remaining_q;
    logic [31:0] length_q;
    logic signed [31:0] bias_q, result_q;
    logic done_q;
    logic [1:0] error_q;

    logic busy, input_fire, valid_mask, final_beat;
    logic [LANES-1:0] expected_mask;
    logic start_good, bad_length, write_length, write_bias, clear_errors;
    logic length_valid;
    logic [16*LANES-1:0] lane_products;
    logic signed [17:0] beat_delta;

    if ((LANES != 1 && LANES != 4) || MAX_VECTOR_LENGTH < 1 || MAX_VECTOR_LENGTH > 1024) begin : invalid_parameters
        initial $fatal(1, "EdgeMAC requires LANES=1 or 4 and MAX_VECTOR_LENGTH=1..1024");
    end

    assign busy = state_q != IDLE;
    assign in_ready = rst_n && state_q == RECEIVE;
    assign input_fire = in_valid && in_ready;
    assign valid_mask = in_mask == expected_mask;
    assign final_beat = remaining_q <= LANE_COUNT;
    assign result_valid = rst_n && state_q == HOLD;
    assign result_data = result_q;

    for (genvar lane = 0; lane < LANES; lane++) begin : datapath_lane
        logic signed [7:0] activation, weight;
        assign expected_mask[lane] = remaining_q > COUNT_WIDTH'(lane);
        assign activation = $signed(activations[8*lane +: 8]);
        assign weight = $signed(weights[8*lane +: 8]);
        assign lane_products[16*lane +: 16] =
            in_mask[lane] ? 16'(activation) * 16'(weight) : 16'sd0;
    end

    if (LANES == 4) begin : four_lane_sum
        logic signed [16:0] pair_01, pair_23;
        assign pair_01 = 17'($signed(lane_products[15:0])) + 17'($signed(lane_products[31:16]));
        assign pair_23 = 17'($signed(lane_products[47:32])) + 17'($signed(lane_products[63:48]));
        assign beat_delta = 18'(pair_01) + 18'(pair_23);
    end else begin : one_lane_sum
        assign beat_delta = 18'($signed(lane_products[15:0]));
    end

    assign next_total = accumulator_q + total_t'(beat_delta);

    function automatic logic signed [31:0] saturate(input total_t value);
        if (value > 33'sd2147483647)
            saturate = 32'sh7fff_ffff;
        else if (value < -33'sd2147483648)
            saturate = 32'sh8000_0000;
        else
            saturate = $signed(value[31:0]);
    endfunction

    assign cfg_ready = rst_n;
    assign length_valid = length_q != 32'd0 && length_q <= LENGTH_LIMIT;

    always_comb begin
        cfg_rdata = 32'd0;
        cfg_error = 1'b0;
        start_good = 1'b0;
        bad_length = 1'b0;
        write_length = 1'b0;
        write_bias = 1'b0;
        clear_errors = 1'b0;

        if (cfg_valid && cfg_ready) begin
            case (cfg_addr)
                8'h00: begin
                    if (!cfg_write || cfg_wdata != 32'd1 || busy) begin
                        cfg_error = 1'b1;
                    end else begin
                        start_good = length_valid;
                        bad_length = !length_valid;
                        cfg_error = !length_valid;
                    end
                end
                8'h04: begin
                    if (cfg_write)
                        cfg_error = 1'b1;
                    else
                        cfg_rdata = {29'd0, done_q, result_valid, busy};
                end
                8'h08: begin
                    if (cfg_write) begin
                        cfg_error = busy;
                        write_length = !busy;
                    end else begin
                        cfg_rdata = length_q;
                    end
                end
                8'h0c: begin
                    if (cfg_write) begin
                        cfg_error = busy;
                        write_bias = !busy;
                    end else begin
                        cfg_rdata = bias_q;
                    end
                end
                8'h10: begin
                    if (cfg_write)
                        cfg_error = 1'b1;
                    else
                        cfg_rdata = result_q;
                end
                8'h14: begin
                    if (cfg_write) begin
                        cfg_error = (cfg_wdata & 32'hffff_fffc) != 32'd0;
                        clear_errors = !cfg_error;
                    end else begin
                        cfg_rdata = {30'd0, error_q};
                    end
                end
                default: cfg_error = 1'b1;
            endcase
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state_q <= IDLE;
            accumulator_q <= '0;
            remaining_q <= '0;
            length_q <= '0;
            bias_q <= '0;
            result_q <= '0;
            done_q <= 1'b0;
            error_q <= '0;
        end else begin
            if (write_length)
                length_q <= cfg_wdata;
            if (write_bias)
                bias_q <= $signed(cfg_wdata);

            // Hardware errors take priority over same-edge software clears.
            error_q[0] <= bad_length || (error_q[0] && !(clear_errors && cfg_wdata[0]));
            error_q[1] <= (input_fire && !valid_mask) || (error_q[1] && !(clear_errors && cfg_wdata[1]));

            case (state_q)
                IDLE: begin
                    if (start_good) begin
                        remaining_q <= COUNT_WIDTH'(length_q);
                        accumulator_q <= total_t'(bias_q);
                        done_q <= 1'b0;
                        state_q <= RECEIVE;
                    end
                end
                RECEIVE: begin
                    if (input_fire) begin
                        if (!valid_mask) begin
                            remaining_q <= '0;
                            state_q <= IDLE;
                        end else if (final_beat) begin
                            remaining_q <= '0;
                            result_q <= saturate(next_total);
                            done_q <= 1'b1;
                            state_q <= HOLD;
                        end else begin
                            remaining_q <= remaining_q - LANE_COUNT;
                            accumulator_q <= next_total;
                        end
                    end
                end
                HOLD: begin
                    if (result_ready)
                        state_q <= IDLE;
                end
                default: begin
                    remaining_q <= '0;
                    state_q <= IDLE;
                end
            endcase
        end
    end
endmodule
