module edgemac_arithmetic #(
    parameter integer LANES = 1,
    parameter integer OPERAND_BITS = 2
) (
    input wire clk
);
    localparam integer LENGTH = LANES + 1;

    (* anyconst *) reg signed [31:0] arbitrary_bias;
    localparam integer B = OPERAND_BITS;
    (* anyconst *) reg [B*LANES-1:0] first_activations, first_weights;
    (* anyconst *) reg [B*LANES-1:0] tail_activations, tail_weights;

    reg [3:0] phase = 0;
    always @(posedge clk) begin
        if (phase != 9)
            phase <= phase + 1'b1;
    end

    wire rst_n = (phase != 0);
    wire cfg_valid = (phase >= 1 && phase <= 3);
    wire cfg_write = 1'b1;
    wire [7:0] cfg_addr = (phase == 1) ? 8'h08 : (phase == 2) ? 8'h0c : 8'h00;
    wire [31:0] cfg_wdata = (phase == 1) ? LENGTH : (phase == 2) ? arbitrary_bias : 32'd1;
    wire cfg_ready, cfg_error;
    wire [31:0] cfg_rdata;

    wire in_valid = (phase == 4 || phase == 5);
    wire in_ready;
    wire [LANES-1:0] in_mask = (phase == 4) ? {LANES{1'b1}} : LANES'(1);
    wire [8*LANES-1:0] activations, weights;

    wire result_ready = (phase == 7);
    wire result_valid;
    wire signed [31:0] result_data;

    for (genvar lane = 0; lane < LANES; lane = lane + 1) begin : symbolic_lanes
        wire signed [B-1:0] activation = (phase == 4) ?
            first_activations[B*lane +: B] : tail_activations[B*lane +: B];
        wire signed [B-1:0] weight = (phase == 4) ?
            first_weights[B*lane +: B] : tail_weights[B*lane +: B];
        assign activations[8*lane +: 8] = 8'(activation);
        assign weights[8*lane +: 8] = 8'(weight);
    end

    edgemac_top #(.LANES(LANES), .MAX_VECTOR_LENGTH(LENGTH)) dut (.*);

    // Scalar specification: sum signed pairs in index order, then add bias once.
    // The formal domain is all signed OPERAND_BITS-wide operands (2 or the full 8)
    // and all signed INT32 biases.
    function automatic signed [33:0] dot_pair(input [B-1:0] a, input [B-1:0] w);
        dot_pair = $signed(a) * $signed(w);
    endfunction

    reg signed [33:0] mathematical_sum;
    always @* begin
        mathematical_sum = dot_pair(tail_activations[B-1:0], tail_weights[B-1:0]);
        for (integer index = 0; index < LANES; index = index + 1)
            mathematical_sum = mathematical_sum
                + dot_pair(first_activations[B*index +: B], first_weights[B*index +: B]);
        mathematical_sum = mathematical_sum + 34'(arbitrary_bias);
    end

    localparam signed [33:0] LOW = -34'sd2147483648;
    localparam signed [33:0] HIGH = 34'sd2147483647;

    always @(posedge clk) begin
        if (cfg_valid)
            assert(cfg_ready && !cfg_error);
        if (in_valid)
            assert(in_ready);
        if (phase >= 1 && phase <= 8)
            assert(result_valid == (phase == 6 || phase == 7));
        if (phase == 6 || phase == 7) begin
            if (mathematical_sum < LOW)
                assert(result_data == 32'sh8000_0000);
            else if (mathematical_sum > HIGH)
                assert(result_data == 32'sh7fff_ffff);
            else
                assert(34'(result_data) == mathematical_sum);
        end

        cover(phase == 6 && result_valid && mathematical_sum < LOW);
        cover(phase == 6 && result_valid && mathematical_sum > HIGH);
        cover(phase == 6 && result_valid && mathematical_sum == 0);
    end
endmodule
