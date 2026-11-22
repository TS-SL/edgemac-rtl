`timescale 1ns/1ps

module signed_multiplier (
    input  logic signed [7:0]  left,
    input  logic signed [7:0]  right,
    output logic signed [15:0] product
);
    assign product = left * right;
endmodule
